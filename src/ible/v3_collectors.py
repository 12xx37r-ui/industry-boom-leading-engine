from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
import urllib.parse

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


class UsaSpendingCollector:
    AWARD_TYPE_CODES = ["02","03","04","05","06","07","08","09","10","11","A","B","C","D","IDV_A","IDV_B","IDV_B_A","IDV_B_B","IDV_B_C","IDV_C","IDV_D","IDV_E"]
    def __init__(self, client: JsonHttpClient, base_url: str) -> None:
        self.client = client
        self.base_url = base_url
    def count(self, keywords: list[str], period: Period) -> int:
        payload = self.client.request_json(self.base_url, method="POST", payload={
            "filters":{"keywords":keywords,"time_period":[period.as_dict()],"award_type_codes":self.AWARD_TYPE_CODES},
            "spending_level":"awards","subawards":False,
        })
        results = payload.get("results") or {}
        return sum(max(0,int(results.get(field) or 0)) for field in ("grants","loans","contracts","direct_payments","other","idvs"))


class GdeltCollector:
    def __init__(self, client: JsonHttpClient, base_url: str) -> None:
        self.client = client
        self.base_url = base_url

    def timeline(self, query: str, period: Period) -> list[float]:
        payload = self.client.request_json(self.base_url, params={
            "query": query,
            "mode": "timelinevolraw",
            "format": "json",
            "startdatetime": period.start.strftime("%Y%m%d000000"),
            "enddatetime": (period.end + timedelta(days=1)).strftime("%Y%m%d000000"),
            "timelinesmooth": 0,
        })
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


class WikimediaCollector:
    def __init__(self, client: JsonHttpClient, base_url: str) -> None:
        self.client = client
        self.base_url = base_url.rstrip("/")

    def timeline(self, titles: list[str], period: Period) -> list[float]:
        total_by_day: dict[str,float] = {}
        successful = 0
        for title in titles:
            article = urllib.parse.quote(str(title).replace(" ","_"), safe="")
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
