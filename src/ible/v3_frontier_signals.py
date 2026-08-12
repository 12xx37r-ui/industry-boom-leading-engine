from __future__ import annotations

"""Bounded frontier-signal collectors for GitHub activity and patents.

These signals are nowcast supplements only. They never replace official data,
never alter the theme universe automatically, and never use observations dated
after the requested ``as_of`` date.
"""

import os
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


def _github_observation(
    client: JsonHttpClient,
    theme: dict[str, Any],
    as_of: date,
    max_repositories: int,
    history: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    theme_id = str(theme.get("theme_id") or "")
    query = _normalise_term(str(theme.get("openalex_search") or theme.get("theme_name") or theme_id))
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
        key = _history_key("github", theme_id, full_name)
        previous = (history.get("observations") or {}).get(key)
        repositories.append({
            "full_name": full_name,
            "html_url": item.get("html_url"),
            "description": str(item.get("description") or "")[:300],
            "stargazers_count": stars,
            "forks_count": max(0, int(item.get("forks_count") or 0)),
            "open_issues_count": max(0, int(item.get("open_issues_count") or 0)),
            "pushed_at": pushed.isoformat() if pushed else None,
            "updated_at": updated.isoformat() if updated else None,
            "star_delta_percent": _safe_growth(stars, previous.get("stargazers_count") if isinstance(previous, dict) else None),
            "observation_date": as_of.isoformat(),
        })
    return {
        "status": "GITHUB_OBSERVED" if repositories else "GITHUB_NO_MATCH",
        "query": query,
        "repositories": repositories,
    }


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
    for theme in selected[: max(0, int(config.get("max_patent_queries_per_run", 3)))]:
        patent_rows.append({
            "theme_id": str(theme.get("theme_id") or ""),
            "theme_name": theme.get("theme_name"),
            "patentsview": _patentsview_observation(client, theme, observed_date, history, config),
        })
    next_observations = dict(history.get("observations") or {})
    patent_counts = dict(history.get("patent_counts") or {})
    for row in github_rows:
        for repo in row["github"].get("repositories") or []:
            next_observations[_history_key("github", row["theme_id"], repo["full_name"])] = {
                "stargazers_count": repo["stargazers_count"],
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
        "history": history_out,
        "lookahead_guard": "FUTURE_DATA_REJECTED",
    }


def write_frontier_signals(root: Path, output_dir: Path, report: dict[str, Any]) -> None:
    report = dict(report)
    report["content_sha256"] = canonical_sha256({key: value for key, value in report.items() if key != "content_sha256"})
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "v3_frontier_signal_observations.json", report)
    write_json(root / "data_cache/latest/v3_frontier_signal_history.json", report["history"])
