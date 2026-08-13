from __future__ import annotations

"""Bounded frontier-signal collectors for GitHub activity and patents.

These signals are nowcast supplements only. They never replace official data,
never alter the theme universe automatically, and never use observations dated
after the requested ``as_of`` date.
"""

import os
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ible.integrity import canonical_sha256, load_json, write_json
from ible.v3_http import HttpError, JsonHttpClient


def _as_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _normalise_term(query: str) -> str:
    words = [word.strip("'\"()") for word in str(query).split() if word.strip("'\"()")]
    return " ".join(words[:6]) or "technology"


def _selected_themes(themes: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    ordered = sorted(themes, key=lambda row: (int(row.get("data_build_priority", 999)), str(row.get("theme_id", ""))))
    return ordered[: max(0, int(limit))]


def _previous_history(root: Path) -> dict[str, Any]:
    path = root / "data_cache/latest/v3_frontier_signal_history.json"
    try:
        payload = load_json(path)
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _history_key(source: str, theme_id: str, identifier: str) -> str:
    return f"{source}:{theme_id}:{identifier}"


def _safe_growth(current: float, previous: Any) -> float | None:
    if previous is None:
        return None
    try:
        old = float(previous)
    except (TypeError, ValueError):
        return None
    return round(100.0 * (float(current) - old) / max(1.0, old), 4)


def _days_since(observed_date: date | None, as_of: date) -> int | None:
    if observed_date is None:
        return None
    return max(0, (as_of - observed_date).days)


def _activity_score(stars: int, forks: int, days_since_push: int | None) -> float:
    """Local heuristic; no extra API call. 0–100 scale."""
    base = min(100.0, 15.0 * (stars + forks * 2 + 1) ** 0.35)
    if days_since_push is not None:
        recency = max(0.0, 1.0 - days_since_push / 365.0)
        return round(_clamp(base * (0.6 + 0.4 * recency)), 4)
    return round(_clamp(base * 0.7), 4)


def _github_observation(
    client: JsonHttpClient,
    theme: dict[str, Any],
    as_of: date,
    max_repositories: int,
    history: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    theme_id = str(theme.get("theme_id") or "")
    # github_search overrides openalex_search when the academic phrasing differs from GitHub conventions
    query = _normalise_term(str(theme.get("github_search") or theme.get("openalex_search") or theme.get("theme_name") or theme_id))
    params = {
        "q": query,
        "sort": "stars",
        "order": "desc",
        "per_page": max(1, min(10, int(max_repositories))),
    }
    headers: dict[str, str] = {}
    token = str(os.getenv("GITHUB_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        payload = client.request_json(
            str(config.get("github_search_url") or "https://api.github.com/search/repositories"),
            params=params,
            headers=headers,
            cache_ttl_seconds=int(config.get("cache_ttl_seconds", 86400)),
        )
    except (HttpError, OSError, ValueError, TypeError) as exc:
        return {
            "status": "GITHUB_UNAVAILABLE_CACHE_PRESERVED",
            "query": query,
            "repositories": [],
            "error": str(exc)[:500],
        }
    repositories: list[dict[str, Any]] = []
    prev_obs: dict[str, Any] = history.get("observations") or {}
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        pushed = _as_date(item.get("pushed_at"))
        updated = _as_date(item.get("updated_at"))
        observed = pushed or updated
        if observed and observed > as_of:
            continue
        full_name = str(item.get("full_name") or item.get("html_url") or "").strip()
        if not full_name:
            continue
        stars = max(0, int(item.get("stargazers_count") or 0))
        forks = max(0, int(item.get("forks_count") or 0))
        days_push = _days_since(pushed, as_of)
        key = _history_key("github", theme_id, full_name)
        previous = prev_obs.get(key)
        prev_stars = previous.get("stargazers_count") if isinstance(previous, dict) else None
        prev_forks = previous.get("forks_count") if isinstance(previous, dict) else None
        repositories.append({
            "full_name": full_name,
            "html_url": item.get("html_url"),
            "description": str(item.get("description") or "")[:300],
            "stargazers_count": stars,
            "forks_count": forks,
            "open_issues_count": max(0, int(item.get("open_issues_count") or 0)),
            "pushed_at": pushed.isoformat() if pushed else None,
            "updated_at": updated.isoformat() if updated else None,
            "days_since_push": days_push,
            "star_delta_percent": _safe_growth(stars, prev_stars),
            "fork_delta_percent": _safe_growth(forks, prev_forks),
            "activity_score": _activity_score(stars, forks, days_push),
            "observation_date": as_of.isoformat(),
        })
    if repositories:
        return {"status": "GITHUB_OBSERVED", "query": query, "repositories": repositories}

    # If no results and query is long, retry with first 3 words (cached on next run)
    words = query.split()
    if len(words) > 3:
        short_query = " ".join(words[:3])
        params2 = dict(params)
        params2["q"] = short_query
        try:
            payload2 = client.request_json(
                str(config.get("github_search_url") or "https://api.github.com/search/repositories"),
                params=params2,
                headers=headers,
                cache_ttl_seconds=int(config.get("cache_ttl_seconds", 86400)),
            )
            for item in payload2.get("items") or []:
                if not isinstance(item, dict):
                    continue
                pushed = _as_date(item.get("pushed_at"))
                updated = _as_date(item.get("updated_at"))
                observed = pushed or updated
                if observed and observed > as_of:
                    continue
                full_name = str(item.get("full_name") or item.get("html_url") or "").strip()
                if not full_name:
                    continue
                stars = max(0, int(item.get("stargazers_count") or 0))
                forks = max(0, int(item.get("forks_count") or 0))
                days_push = _days_since(pushed, as_of)
                key = _history_key("github", theme_id, full_name)
                previous = prev_obs.get(key)
                repositories.append({
                    "full_name": full_name,
                    "html_url": item.get("html_url"),
                    "description": str(item.get("description") or "")[:300],
                    "stargazers_count": stars,
                    "forks_count": forks,
                    "open_issues_count": max(0, int(item.get("open_issues_count") or 0)),
                    "pushed_at": pushed.isoformat() if pushed else None,
                    "updated_at": updated.isoformat() if updated else None,
                    "days_since_push": days_push,
                    "star_delta_percent": _safe_growth(stars, previous.get("stargazers_count") if isinstance(previous, dict) else None),
                    "fork_delta_percent": _safe_growth(forks, previous.get("forks_count") if isinstance(previous, dict) else None),
                    "activity_score": _activity_score(stars, forks, days_push),
                    "observation_date": as_of.isoformat(),
                })
        except (HttpError, OSError, ValueError, TypeError):
            pass
        if repositories:
            return {"status": "GITHUB_OBSERVED", "query": short_query, "original_query": query, "repositories": repositories}

    return {"status": "GITHUB_NO_MATCH", "query": query, "repositories": []}


def _patent_query(term: str, as_of: date, lookback_days: int) -> dict[str, Any]:
    start = as_of - timedelta(days=max(1, int(lookback_days)) - 1)
    return {
        "_and": [
            {"_text_any": {"patent_title": term}},
            {"_gte": {"patent_date": start.isoformat()}},
            {"_lte": {"patent_date": as_of.isoformat()}},
        ]
    }


def _patentsview_observation(
    client: JsonHttpClient,
    theme: dict[str, Any],
    as_of: date,
    history: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    key = str(os.getenv("PATENTSVIEW_API_KEY") or os.getenv("USPTO_API_KEY") or "").strip()
    term = _normalise_term(str(theme.get("openalex_search") or theme.get("theme_name") or theme.get("theme_id")))
    if not key:
        return {
            "status": "WAITING_FOR_PATENTSVIEW_API_KEY",
            "query": term,
            "patent_count": None,
            "external_call_allowed": False,
            "reason": "PATENTSVIEW_API_KEY or USPTO_API_KEY is not configured",
        }
    payload = {
        "q": _patent_query(term, as_of, int(config.get("patent_lookback_days", 365))),
        "f": ["patent_id", "patent_date", "patent_title"],
        "o": {"size": 1, "include_sub_entity": False},
    }
    headers = {"X-Api-Key": key}
    try:
        response = client.request_json(
            str(config.get("patentsview_url") or "https://search.patentsview.org/api/v1/patent/"),
            method="POST",
            payload=payload,
            headers=headers,
            cache_ttl_seconds=int(config.get("cache_ttl_seconds", 86400)),
        )
    except (HttpError, OSError, ValueError, TypeError) as exc:
        return {
            "status": "PATENTSVIEW_UNAVAILABLE_CACHE_PRESERVED",
            "query": term,
            "patent_count": None,
            "external_call_allowed": True,
            "error": str(exc)[:500],
        }
    result = response.get("results") or {}
    count = result.get("total_patent_count", result.get("count"))
    if count is None and isinstance(result.get("patents"), list):
        count = len(result["patents"])
    try:
        count_value = max(0, int(count or 0))
    except (TypeError, ValueError):
        count_value = 0
    previous = (history.get("patent_counts") or {}).get(str(theme.get("theme_id") or ""))
    return {
        "status": "PATENTSVIEW_OBSERVED",
        "query": term,
        "patent_count": count_value,
        "patent_count_delta_percent": _safe_growth(count_value, previous),
        "observation_date": as_of.isoformat(),
        "external_call_allowed": True,
    }


_PATENT_COUNT_KEYS = frozenset({
    "total_num_results", "total_results", "result_count", "num_results",
    "total", "count", "numResults", "resultsCount", "patentCount",
    "results_count", "patent_count", "totalCount", "total_count",
})
_PATENT_COUNT_RE = re.compile(
    r'"(?:total_num_results|total_results|result_count|num_results|total|count'
    r'|numResults|resultsCount|patentCount|results_count|totalCount|total_count)'
    r'"\s*:\s*(\d+)'
)


def _extract_patent_count(data: Any, depth: int = 0) -> int | None:
    """Traverse a parsed JSON structure looking for a patent-count-like integer."""
    if depth > 6:
        return None
    if isinstance(data, dict):
        for key in _PATENT_COUNT_KEYS:
            val = data.get(key)
            if isinstance(val, int) and val >= 0:
                return val
        for val in data.values():
            result = _extract_patent_count(val, depth + 1)
            if result is not None:
                return result
    elif isinstance(data, list):
        for item in data:
            result = _extract_patent_count(item, depth + 1)
            if result is not None:
                return result
    return None


def _google_patents_observation(client: JsonHttpClient, term: str, as_of: date, config: dict[str, Any]) -> dict[str, Any]:
    """Best-effort, no-key Google Patents search; handles JSON or HTML response conservatively."""
    base = str(config.get("google_patents_url") or "https://patents.google.com/xhr/query")
    ttl = int(config.get("cache_ttl_seconds", 86400))
    params = {"url": f"q={term}", "exp": "", "download": "false"}
    raw_text: str | None = None
    try:
        raw_text = client.request_text(base, params=params, accept="application/json,text/html,*/*", cache_ttl_seconds=ttl)
    except (HttpError, OSError, ValueError, TypeError):
        pass
    if raw_text:
        # Try JSON structure traversal first (exact field match)
        try:
            parsed = json.loads(raw_text)
            count = _extract_patent_count(parsed)
            if count is not None:
                return {"status": "GOOGLE_PATENTS_OBSERVED", "query": term, "patent_count": count, "observation_date": as_of.isoformat(), "external_call_allowed": True}
        except (ValueError, TypeError):
            pass
        # Regex fallback for embedded JSON fragments in HTML
        matches = _PATENT_COUNT_RE.findall(raw_text)
        if matches:
            # Pick the largest value to avoid matching internal sub-counts
            best = max(int(m) for m in matches)
            if best > 0:
                return {"status": "GOOGLE_PATENTS_OBSERVED", "query": term, "patent_count": best, "observation_date": as_of.isoformat(), "external_call_allowed": True}
    return {"status": "GOOGLE_PATENTS_UNAVAILABLE", "query": term, "patent_count": None, "external_call_allowed": False}


def _uspto_bulk_cache_observation(root: Path, theme_id: str, term: str, as_of: date) -> dict[str, Any]:
    """Read an optional locally downloaded USPTO bulk summary; never downloads bulk files in the live run."""
    candidates = [root / "data_cache/latest/uspto_patent_observations.json", root / "data_cache/latest/uspto_bulk_patent_observations.json"]
    for path in candidates:
        try:
            payload = load_json(path)
        except (OSError, ValueError, TypeError):
            continue
        for row in payload.get("themes") or payload.get("observations") or []:
            if str(row.get("theme_id") or "") == theme_id and _as_date(row.get("as_of")) and _as_date(row.get("as_of")) <= as_of:
                return {"status": "USPTO_BULK_CACHE_OBSERVED", "query": term, "patent_count": int(row.get("patent_count") or 0), "observation_date": str(row.get("as_of"))[:10], "external_call_allowed": False, "input_path": str(path.relative_to(root))}
    return {"status": "WAITING_FOR_USPTO_BULK_CACHE", "query": term, "patent_count": None, "external_call_allowed": False, "reason": "download USPTO bulk data locally and place a summary in data_cache/latest/uspto_patent_observations.json"}


def _bigquery_patents_observation(
    term: str,
    as_of: date,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Query Google Patents Public Data via BigQuery (free 1TB/month, no IP block, no ID.me).

    Setup (one-time, ~10 min):
      1. https://console.cloud.google.com → Create project → Enable BigQuery API
      2. IAM → Service Accounts → Create → grant "BigQuery Job User" + "BigQuery Data Viewer"
      3. Keys → Add Key → JSON → download
      4. GitHub Secrets: GCP_CREDENTIALS_JSON = paste entire JSON content
    Dataset: patents-public-data.patents.publications (USPTO+EPO+JPO+KIPO all included)
    """
    creds_json = str(os.getenv("GCP_CREDENTIALS_JSON") or "").strip()
    if not creds_json:
        return {
            "status": "WAITING_FOR_GCP_CREDENTIALS",
            "query": term,
            "patent_count": None,
            "external_call_allowed": False,
            "reason": "GCP_CREDENTIALS_JSON not configured — see https://console.cloud.google.com",
        }
    try:
        from google.cloud import bigquery  # type: ignore[import-untyped]
        from google.oauth2 import service_account  # type: ignore[import-untyped]
        creds_info = json.loads(creds_json)
        credentials = service_account.Credentials.from_service_account_info(
            creds_info,
            scopes=["https://www.googleapis.com/auth/bigquery"],
        )
        bq = bigquery.Client(credentials=credentials, project=creds_info["project_id"])
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "BIGQUERY_AUTH_FAILED",
            "query": term,
            "patent_count": None,
            "external_call_allowed": True,
            "bq_error": str(exc)[:500],
        }
    lookback = int(config.get("patent_lookback_days", 365))
    start_int = int((as_of - timedelta(days=lookback)).strftime("%Y%m%d"))
    end_int = int(as_of.strftime("%Y%m%d"))
    # Build simple regex from top keywords (avoid full-table UNNEST explosion)
    words = [w for w in re.split(r"\s+", term) if len(w) > 3][:4]
    pattern = "|".join(re.escape(w.lower()) for w in words) if words else re.escape(term.lower())
    sql = """
SELECT COUNT(*) AS cnt
FROM `patents-public-data.patents.publications`
WHERE publication_date BETWEEN @start_date AND @end_date
  AND REGEXP_CONTAINS(
        LOWER(COALESCE(title[SAFE_OFFSET(0)].text, '')),
        @pattern
      )
  AND country_code IN ('US', 'EP', 'WO', 'KR', 'JP')
"""
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("start_date", "INT64", start_int),
            bigquery.ScalarQueryParameter("end_date", "INT64", end_int),
            bigquery.ScalarQueryParameter("pattern", "STRING", pattern),
        ]
    )
    try:
        rows = list(bq.query(sql, job_config=job_config).result(timeout=30))
        count = int((rows[0]["cnt"] if rows else 0) or 0)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "BIGQUERY_QUERY_FAILED",
            "query": term,
            "patent_count": None,
            "external_call_allowed": True,
            "bq_error": str(exc)[:500],
        }
    finally:
        try:
            bq.close()
        except Exception:  # noqa: BLE001
            pass
    return {
        "status": "BIGQUERY_OBSERVED",
        "query": term,
        "patent_count": count,
        "observation_date": as_of.isoformat(),
        "external_call_allowed": True,
    }


def _patent_fallback_observation(root: Path, client: JsonHttpClient, theme: dict[str, Any], as_of: date, history: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    term = _normalise_term(str(theme.get("openalex_search") or theme.get("theme_name") or theme.get("theme_id")))
    skipped: dict[str, str] = {}
    google = _google_patents_observation(client, term, as_of, config)
    if google.get("patent_count") is not None:
        google["provider_chain"] = ["google_patents"]
        return google
    skipped["google_patents"] = google.get("status", "UNAVAILABLE")
    bq = _bigquery_patents_observation(term, as_of, config)
    if bq.get("patent_count") is not None:
        bq["provider_chain"] = ["google_patents", "bigquery"]
        return bq
    skipped["bigquery"] = bq.get("status", "UNAVAILABLE")
    if bq.get("bq_error"):
        skipped["bigquery_error"] = bq["bq_error"]
    bulk = _uspto_bulk_cache_observation(root, str(theme.get("theme_id") or ""), term, as_of)
    if bulk.get("patent_count") is not None:
        bulk["provider_chain"] = ["google_patents", "bigquery", "uspto_bulk_cache"]
        return bulk
    skipped["uspto_bulk_cache"] = bulk.get("status", "UNAVAILABLE")
    openalex_url = str(config.get("openalex_url") or "https://api.openalex.org/works")
    try:
        response = client.request_json(openalex_url, params={"search": term, "filter": f"from_publication_date:{(as_of - timedelta(days=364)).isoformat()},to_publication_date:{as_of.isoformat()}", "per-page": 1, "select": "id"}, cache_ttl_seconds=int(config.get("cache_ttl_seconds", 86400)))
        count = int((response.get("meta") or {}).get("count") or 0)
        return {"status": "OPENALEX_PROXY_OBSERVED", "query": term, "patent_count": None, "related_work_count": count, "proxy_type": "RELATED_SCHOLARLY_WORKS_NOT_PATENTS", "observation_date": as_of.isoformat(), "external_call_allowed": True, "provider_chain": ["google_patents", "bigquery", "uspto_bulk_cache", "openalex_proxy"], "skipped_providers": skipped}
    except (HttpError, OSError, ValueError, TypeError) as exc:
        return {"status": "ALL_PATENT_FALLBACKS_UNAVAILABLE", "query": term, "patent_count": None, "external_call_allowed": False, "provider_chain": ["google_patents", "bigquery", "uspto_bulk_cache", "openalex_proxy"], "skipped_providers": skipped, "error": str(exc)[:500]}


def _kipris_observation(
    client: JsonHttpClient,
    term: str,
    as_of: date,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Query KIPRIS Plus (Korean Intellectual Property Rights Information Service).

    Covers domestic KR patents filed by Samsung, SK Hynix, LG, Hyundai, etc.
    Registration: https://plus.kipris.or.kr → 회원가입 → API 활용신청 (무료)
    Set KIPRIS_API_KEY in GitHub Secrets.
    """
    key = str(os.getenv("KIPRIS_API_KEY") or "").strip()
    if not key:
        return {
            "status": "WAITING_FOR_KIPRIS_API_KEY",
            "query": term,
            "korean_patent_count": None,
            "external_call_allowed": False,
            "reason": "KIPRIS_API_KEY not configured — register free at https://plus.kipris.or.kr",
        }
    lookback = int(config.get("patent_lookback_days", 365))
    start_dt = (as_of - timedelta(days=lookback)).strftime("%Y%m%d")
    end_dt = as_of.strftime("%Y%m%d")
    # Use top 3 keywords for search; KIPRIS full-text search has limited query length
    words = [w for w in re.split(r"\s+", term) if len(w) > 2][:3]
    search_word = " ".join(words) if words else term
    base_url = str(config.get("kipris_url") or "https://plus.kipris.or.kr/openapi/rest/patUtiModInfoSearchSevice/getWordSearch")
    try:
        raw = client.request_text(
            base_url,
            params={
                "word": search_word,
                "docsStart": "1",
                "docsCount": "1",
                "applicationStartDate": start_dt,
                "applicationEndDate": end_dt,
                "ServiceKey": key,
                "type": "json",
            },
            cache_ttl_seconds=int(config.get("cache_ttl_seconds", 86400)),
        )
        response = json.loads(raw)
    except (HttpError, OSError, ValueError, TypeError) as exc:
        return {
            "status": "KIPRIS_UNAVAILABLE",
            "query": search_word,
            "korean_patent_count": None,
            "external_call_allowed": True,
            "kipris_error": str(exc)[:500],
        }
    try:
        body = (response.get("response") or response).get("body") or {}
        count = int(body.get("totalCount") or body.get("numOfRows") or 0)
    except (TypeError, ValueError, AttributeError):
        count = 0
    return {
        "status": "KIPRIS_OBSERVED",
        "query": search_word,
        "korean_patent_count": count,
        "observation_date": as_of.isoformat(),
        "external_call_allowed": True,
        "note": "KR domestic patents only (KIPRIS Plus)",
    }


def build_frontier_signals(
    root: Path,
    themes: list[dict[str, Any]],
    as_of: str,
    client: JsonHttpClient,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = config or {}
    observed_date = _as_date(as_of)
    if observed_date is None:
        raise ValueError(f"invalid as_of: {as_of}")
    history = _previous_history(root)
    selected = _selected_themes(themes, int(config.get("max_theme_queries_per_run", 3)))
    github_rows = []
    for theme in selected:
        github_rows.append({
            "theme_id": str(theme.get("theme_id") or ""),
            "theme_name": theme.get("theme_name"),
            "github": _github_observation(client, theme, observed_date, int(config.get("max_repositories_per_theme", 2)), history, config),
        })
    patent_rows = []
    kipris_rows = []
    for theme in selected[: max(0, int(config.get("max_patent_queries_per_run", 3)))]:
        patent_fallback = _patent_fallback_observation(root, client, theme, observed_date, history, config)
        patent_rows.append({
            "theme_id": str(theme.get("theme_id") or ""),
            "theme_name": theme.get("theme_name"),
            "patentsview": _patentsview_observation(client, theme, observed_date, history, config) if (os.getenv("PATENTSVIEW_API_KEY") or os.getenv("USPTO_API_KEY")) else patent_fallback,
        })
        term = _normalise_term(str(theme.get("openalex_search") or theme.get("theme_name") or theme.get("theme_id")))
        kipris_rows.append({
            "theme_id": str(theme.get("theme_id") or ""),
            "theme_name": theme.get("theme_name"),
            "kipris": _kipris_observation(client, term, observed_date, config),
        })
    next_observations = dict(history.get("observations") or {})
    patent_counts = dict(history.get("patent_counts") or {})
    for row in github_rows:
        for repo in row["github"].get("repositories") or []:
            next_observations[_history_key("github", row["theme_id"], repo["full_name"])] = {
                "stargazers_count": repo["stargazers_count"],
                "forks_count": repo["forks_count"],
                "observation_date": observed_date.isoformat(),
            }
    for row in patent_rows:
        result = row["patentsview"]
        if result.get("status") == "PATENTSVIEW_OBSERVED":
            patent_counts[row["theme_id"]] = result.get("patent_count")
    history_out = {
        "schema_version": 1,
        "as_of": observed_date.isoformat(),
        "observations": next_observations,
        "patent_counts": patent_counts,
    }
    return {
        "schema_version": 1,
        "as_of": observed_date.isoformat(),
        "status": "FRONTIER_SIGNALS_PARTIAL" if any(
            row["patentsview"]["status"] != "PATENTSVIEW_OBSERVED" for row in patent_rows
        ) else "FRONTIER_SIGNALS_OBSERVED",
        "investment_use_allowed": False,
        "official_statistics_replaced": False,
        "selected_theme_count": len(selected),
        "github_query_count": len(selected),
        "github_repository_limit_per_theme": int(config.get("max_repositories_per_theme", 2)),
            "patent_query_count": len(patent_rows),
        "patent_query_limit": int(config.get("max_patent_queries_per_run", 3)),
        "github": github_rows,
        "patentsview": patent_rows,
        "kipris": kipris_rows,
        "history": history_out,
        "lookahead_guard": "FUTURE_DATA_REJECTED",
    }


def write_frontier_signals(root: Path, output_dir: Path, report: dict[str, Any]) -> None:
    report = dict(report)
    report["content_sha256"] = canonical_sha256({key: value for key, value in report.items() if key != "content_sha256"})
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "v3_frontier_signal_observations.json", report)
    cache_latest = root / "data_cache/latest"
    cache_latest.mkdir(parents=True, exist_ok=True)
    write_json(cache_latest / "v3_frontier_signal_observations.json", report)
    write_json(cache_latest / "v3_frontier_signal_history.json", report["history"])
