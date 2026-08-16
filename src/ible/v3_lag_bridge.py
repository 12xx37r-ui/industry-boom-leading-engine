from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from ible.integrity import canonical_sha256, write_json


def _freshness(source: dict[str, Any], requested_as_of: str) -> tuple[str, int | None]:
    source_as_of = str(source.get("as_of") or "")[:10]
    try:
        lag_days = (date.fromisoformat(requested_as_of[:10]) - date.fromisoformat(source_as_of)).days
    except (TypeError, ValueError):
        return "UNKNOWN_VINTAGE", None
    if lag_days < 0:
        return "FUTURE_DATA_REJECTED", lag_days
    if lag_days <= 7:
        return "CURRENT_PROXY", lag_days
    if lag_days <= 31:
        return "RECENT_PROXY", lag_days
    return "STALE_PROXY", lag_days


def build_lag_bridge(observations: dict[str, Any], requested_as_of: str) -> dict[str, Any]:
    weights = {
        "openalex": 0.30,
        "usaspending": 0.25,
        "naver_search_trend": 0.20,
        "gdelt": 0.15,
    }
    themes = []
    invalid_future_count = 0
    nowcast_count = 0
    for row in observations.get("themes") or []:
        values = []
        source_status = {}
        for name, weight in weights.items():
            source = (row.get("sources") or {}).get(name) or {}
            score = source.get("source_signal_score")
            if score is None:
                score = source.get("attention_score")
            freshness, lag_days = _freshness(source, requested_as_of)
            source_status[name] = {
                "status": source.get("status"),
                "freshness": freshness,
                "lag_days": lag_days,
                "score_used": freshness != "UNKNOWN_VINTAGE" and score is not None,
            }
            if freshness == "FUTURE_DATA_REJECTED":
                invalid_future_count += 1
                continue
            if freshness != "UNKNOWN_VINTAGE" and score is not None:
                values.append((float(score), weight))
        denominator = sum(weight for _, weight in values)
        score = sum(value * weight for value, weight in values) / denominator if denominator else None
        if len(values) >= 2 and score is not None:
            status = "PROXY_NOWCAST_ACTIVE"
            nowcast_count += 1
        elif values:
            status = "PROXY_NOWCAST_PARTIAL"
        else:
            status = "NO_NOWCAST_SOURCE"
        themes.append({
            "theme_id": row.get("theme_id"),
            "status": status,
            "nowcast_score": round(score, 4) if score is not None else None,
            "available_proxy_source_count": len(values),
            "source_status": source_status,
            "official_statistics_replaced": False,
        })
    result = {
        "schema_version": 1,
        "as_of": str(requested_as_of),
        "status": "PROXY_NOWCAST_ACTIVE" if nowcast_count else "PROXY_NOWCAST_UNAVAILABLE",
        "theme_count": len(themes),
        "nowcast_active_theme_count": nowcast_count,
        "future_data_rejected_count": invalid_future_count,
        "official_statistics_replaced": False,
        "investment_use_allowed": False,
        "lookahead_guard": "PASSED" if invalid_future_count == 0 else "FUTURE_DATA_REJECTED",
        "themes": themes,
    }
    result["content_sha256"] = canonical_sha256(result)
    return result


def write_lag_bridge(root: Path, output_dir: Path, bridge: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "v3_nowcast_lag_bridge.json", bridge)
    write_json(root / "data_cache/latest/v3_nowcast_lag_bridge.json", bridge)
