from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
import urllib.parse
import threading
import time
import xml.etree.ElementTree as ET

from ible.v3_http import JsonHttpClient


@dataclass(frozen=True)
class Period:
    start: date
    end: date

    def as_dict(self) -> dict[str, str]:
        return {"start_date": self.start.isoformat(), "end_date": self.end.isoformat()}


def comparison_periods(as_of: date, lookback_days: int) -> tuple[Period, Period]:
    recent_end = as_of
    recent_start = recent_end - timedelta(days=lookback_days - 1)
    prior_end = recent_start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=lookback_days - 1)
    return Period(recent_start, recent_end), Period(prior_start, prior_end)


def three_attention_periods(as_of: date, window_days: int = 30) -> tuple[Period, Period, Period]:
    recent = Period(as_of - timedelta(days=window_days - 1), as_of)
    prior = Period(recent.start - timedelta(days=window_days), recent.start - timedelta(days=1))
    oldest = Period(prior.start - timedelta(days=window_days), prior.start - timedelta(days=1))
    return recent, prior, oldest


class OpenAlexCollector:
    def __init__(self, client: JsonHttpClient, base_url: str) -> None:
        self.client = client
        self.base_url = base_url

    def count(self, query: str, period: Period) -> int:
        payload = self.client.request_json(self.base_url, params={
            "search": query,
            "filter": f"from_publication_date:{period.start.isoformat()},to_publication_date:{period.end.isoformat()}",
            "per-page": 1,
            "select": "id",
        })
        return max(0, int((payload.get("meta") or {}).get("count") or 0))

    def documents(self, query: str, period: Period, limit: int = 5) -> list[dict[str, Any]]:
        payload = self.client.request_json(self.base_url, params={
            "search": query,
            "filter": f"from_publication_date:{period.start.isoformat()},to_publication_date:{period.end.isoformat()}",
            "per-page": max(1, min(25, int(limit))),
            "select": "id,title,publication_date,abstract_inverted_index",
        })
        documents = []
        for row in payload.get("results") or []:
            abstract_index = row.get("abstract_inverted_index") or {}
            words = [(position, word) for word, positions in abstract_index.items() for position in (positions or [])]
            abstract = " ".join(word for _, word in sorted(words))
            text = " ".join(value for value in (row.get("title"), abstract) if value)
            if text.strip():
                documents.append({
                    "document_id": str(row.get("id") or ""),
                    "source": "openalex",
                    "captured_at": str(row.get("publication_date") or period.end.isoformat()),
                    "text": text,
                })
        return documents


class ArxivCollector:
    BASE_URL = "https://export.arxiv.org/api/query"
    NS = {"atom": "http://www.w3.org/2005/Atom"}

    def __init__(self, client: JsonHttpClient, base_url: str = BASE_URL) -> None:
        self.client = client
        self.base_url = base_url

    def documents(self, query: str, period: Period, limit: int = 5) -> list[dict[str, Any]]:
        clean_query = str(query).replace('"', "")
        text = self.client.request_text(self.base_url, params={
            "search_query": f'all:"{clean_query}"',
            "start": 0,
            "max_results": max(1, min(25, int(limit))),
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }, accept="application/atom+xml,application/xml,text/xml,*/*", cache_ttl_seconds=86400)
        root = ET.fromstring(text)
        documents = []
        for entry in root.findall("atom:entry", self.NS):
            title = " ".join((entry.findtext("atom:title", "", self.NS) or "").split())
            abstract = " ".join((entry.findtext("atom:summary", "", self.NS) or "").split())
            published = (entry.findtext("atom:published", "", self.NS) or "")[:10]
            identifier = entry.findtext("atom:id", "", self.NS) or title
            text_value = " ".join(value for value in (title, abstract) if value)
            if text_value:
                documents.append({
                    "document_id": identifier,
                    "source": "arxiv",
                    "captured_at": published or period.end.isoformat(),
                    "text": text_value,
                })
        return documents


class UsaSpendingCollector:
    AWARD_TYPE_CODES = ["02","03","04","05","06","07","08","09","10","11","A","B","C","D","IDV_A","IDV_B","IDV_B_A","IDV_B_B","IDV_B_C","IDV_C","IDV_D","IDV_E"]
    def __init__(self, client: JsonHttpClient, base_url: str) -> None:
        self.client = client
        self.base_url = base_url
    def count(self, keywords: list[str] | None, period: Period, naics_codes: list[str] | None = None) -> int:
        filters = {
            "time_period": [period.as_dict()],
            "award_type_codes": self.AWARD_TYPE_CODES,
        }
        clean_keywords = [str(x).strip() for x in (keywords or []) if str(x).strip()]
        clean_naics = [str(x).strip() for x in (naics_codes or []) if str(x).strip()]
        if clean_keywords:
            filters["keywords"] = clean_keywords
        if clean_naics:
            # USAspending accepts hierarchical NAICS prefixes such as 33 or 3333.
            # This is used only as a lower-specificity fallback when the theme text query
            # returns 0/0 for both comparison windows.
            filters["naics_codes"] = {"require": clean_naics}
        payload = self.client.request_json(self.base_url, method="POST", payload={
            "filters": filters,
            "spending_level": "awards",
            "subawards": False,
        })
        results = payload.get("results") or {}
        return sum(max(0,int(results.get(field) or 0)) for field in ("grants","loans","contracts","direct_payments","other","idvs"))


class GdeltCollector:
    """Rate-limit-aware GDELT DOC 2.0 collector.

    GDELT explicitly rate-limits DOC API callers. All GDELT attempts are globally
    serialized, including retries, so concurrent theme workers cannot accidentally
    interleave retry traffic and violate the service cadence.
    """
    _rate_lock = threading.Lock()
    _last_request_started = 0.0

    def __init__(self, client: JsonHttpClient, base_url: str, *, min_interval_seconds: float = 6.5,
                 max_attempts: int = 2, retry_backoff_seconds: float = 8.0) -> None:
        self.client = client
        self.base_url = base_url
        self.min_interval_seconds = max(5.25, float(min_interval_seconds))
        self.max_attempts = max(1, int(max_attempts))
        self.retry_backoff_seconds = max(self.min_interval_seconds, float(retry_backoff_seconds))

    def _request_json(self, *, params: dict[str, Any]) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            cls = type(self)
            with cls._rate_lock:
                now = time.monotonic()
                delay = self.min_interval_seconds - (now - cls._last_request_started)
                if delay > 0:
                    time.sleep(delay)
                cls._last_request_started = time.monotonic()
                try:
                    # The dedicated GDELT HTTP client is configured for one network
                    # attempt. Retry cadence is controlled here, not inside JsonHttpClient.
                    return self.client.request_json(self.base_url, params=params)
                except Exception as exc:
                    last_exc = exc
            if attempt < self.max_attempts:
                # Extra cooldown after a throttled/failed request. This happens outside
                # the lock; the next GDELT caller still must pass the global slot gate.
                time.sleep(self.retry_backoff_seconds * attempt)
        assert last_exc is not None
        raise last_exc

    def timeline(self, query: str, period: Period) -> list[float]:
        params = {
            "query": query,
            "mode": "timelinevolraw",
            "format": "json",
            "startdatetime": period.start.strftime("%Y%m%d000000"),
            "enddatetime": (period.end + timedelta(days=1)).strftime("%Y%m%d000000"),
            "timelinesmooth": 0,
        }
        payload = self._request_json(params=params)
        points: list[float] = []
        def walk(value: Any) -> None:
            if isinstance(value, dict):
                if any(k in value for k in ("date","datetime","timestamp")) and any(k in value for k in ("value","count","Volume Intensity","volume")):
                    raw = value.get("value", value.get("count", value.get("Volume Intensity", value.get("volume"))))
                    norm = value.get("norm", value.get("All Articles"))
                    try:
                        number = float(raw)
                        if norm not in (None,0,"0"):
                            number = 1000000.0 * number / float(norm)
                        points.append(max(0.0, number))
                    except (TypeError, ValueError, ZeroDivisionError):
                        pass
                for item in value.values(): walk(item)
            elif isinstance(value, list):
                for item in value: walk(item)
        walk(payload)
        if not points:
            raise ValueError("GDELT timeline has no numeric points")
        return points

    def documents(self, query: str, period: Period, limit: int = 5) -> list[dict[str, Any]]:
        params = {
            "query": query,
            "mode": "artlist",
            "format": "json",
            "maxrecords": max(1, min(25, int(limit))),
            "sort": "HybridRel",
            "startdatetime": period.start.strftime("%Y%m%d000000"),
            "enddatetime": (period.end + timedelta(days=1)).strftime("%Y%m%d000000"),
        }
        payload = self._request_json(params=params)
        documents = []
        for row in payload.get("articles") or []:
            text = " ".join(value for value in (row.get("title"), row.get("snippet"), row.get("seendate")) if value)
            if text.strip():
                documents.append({
                    "document_id": str(row.get("url") or row.get("title") or ""),
                    "source": "gdelt",
                    "captured_at": str(row.get("seendate") or period.end.isoformat())[:10],
                    "text": text,
                })
        return documents


class NaverSearchTrendCollector:
    """NAVER DataLab Search Trend collector using a common anchor across batches."""
    def __init__(self, client: JsonHttpClient, base_url: str, client_id: str, client_secret: str) -> None:
        self.client = client
        self.base_url = base_url
        self.headers = {
            "X-NCP-APIGW-API-KEY-ID": client_id,
            "X-NCP-APIGW-API-KEY": client_secret,
        }

    def search(self, groups: list[dict[str, Any]], period: Period, time_unit: str = "date") -> dict[str, list[float]]:
        payload = {
            "startDate": period.start.isoformat(),
            "endDate": period.end.isoformat(),
            "timeUnit": time_unit,
            "keywordGroups": [
                {"groupName": str(g["groupName"]), "keywords": [str(x) for x in g.get("keywords") or []][:20]}
                for g in groups
            ],
        }
        raw = self.client.request_json(
            self.base_url, method="POST", payload=payload, headers=self.headers,
            cache_ttl_seconds=21600,
        )
        out: dict[str, list[float]] = {}
        for item in raw.get("results") or []:
            title = str(item.get("title") or "")
            values: list[float] = []
            for point in item.get("data") or []:
                try:
                    values.append(max(0.0, float(point.get("ratio") or 0.0)))
                except (TypeError, ValueError):
                    values.append(0.0)
            if title and values:
                out[title] = values
        return out


class WikimediaCollector:
    def __init__(self, client: JsonHttpClient, base_url: str) -> None:
        self.client = client
        self.base_url = base_url.rstrip("/")

    def timeline(self, titles: list[str], period: Period) -> list[float]:
        total_by_day: dict[str,float] = {}
        successful = 0
        seen_titles: set[str] = set()
        for title in titles:
            normalized_title = str(title).strip()
            if not normalized_title or normalized_title in seen_titles:
                continue
            seen_titles.add(normalized_title)
            article = urllib.parse.quote(normalized_title.replace(" ","_"), safe="")
            url = f"{self.base_url}/{article}/daily/{period.start.strftime('%Y%m%d')}00/{period.end.strftime('%Y%m%d')}00"
            try:
                payload = self.client.request_json(url)
            except Exception:
                continue
            items = payload.get("items") or []
            for item in items:
                key = str(item.get("timestamp") or "")[:8]
                try: total_by_day[key] = total_by_day.get(key,0.0) + max(0.0,float(item.get("views") or 0))
                except (TypeError,ValueError): pass
            successful += 1
        if successful == 0 or not total_by_day:
            raise ValueError("Wikimedia pageview series unavailable")
        return [total_by_day[k] for k in sorted(total_by_day)]
