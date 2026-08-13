from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from ible.integrity import canonical_sha256, load_json, write_json


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def build_hiring_nowcast(root: Path, themes: list[dict[str, Any]], as_of: str) -> dict[str, Any]:
    observed = _day(as_of)
    if observed is None:
        raise ValueError(f"invalid as_of: {as_of}")
    path = root / "data_cache/inbox/hiring_signal_observations.json"
    if not path.is_file():
        return {
            "schema_version": 1, "as_of": observed.isoformat(),
            "status": "WAITING_FOR_LOCAL_HIRING_CACHE", "observed_theme_count": 0,
            "future_observation_rejected_count": 0, "duplicate_observation_count": 0,
            "investment_use_allowed": False, "input_path": str(path.relative_to(root)), "themes": [],
        }
    try:
        payload = load_json(path)
    except (OSError, ValueError, TypeError) as exc:
        return {"schema_version": 1, "as_of": observed.isoformat(), "status": "HIRING_CACHE_INVALID", "error": str(exc)[:500], "observed_theme_count": 0, "investment_use_allowed": False, "themes": []}
    rows = payload.get("observations") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        rows = []
    unique: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    future = 0
    duplicates = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        item_day = _day(row.get("observed_date"))
        if item_day is None or item_day > observed:
            future += 1
            continue
        theme_id = str(row.get("theme_id") or "")
        source = str(row.get("source") or "local")
        query = str(row.get("query") or "")
        key = (theme_id, source, query, item_day.isoformat())
        if key in unique:
            duplicates += 1
            continue
        try:
            recent = max(0.0, float(row.get("posting_count") or 0))
            prior = max(0.0, float(row.get("prior_posting_count") or 0))
        except (TypeError, ValueError):
            continue
        unique[key] = {"theme_id": theme_id, "source": source, "query": query, "observed_date": item_day.isoformat(), "posting_count": recent, "prior_posting_count": prior, "growth_percent": round(100.0 * (recent - prior) / max(1.0, prior), 4), "source_timestamp": row.get("source_timestamp")}
    by_theme: dict[str, list[dict[str, Any]]] = {}
    for row in unique.values():
        by_theme.setdefault(row["theme_id"], []).append(row)
    result = [{"theme_id": str(theme.get("theme_id") or ""), "observations": by_theme.get(str(theme.get("theme_id") or ""), [])} for theme in themes]
    observed_count = sum(bool(row["observations"]) for row in result)
    return {"schema_version": 1, "as_of": observed.isoformat(), "status": "HIRING_NOWCAST_OBSERVED" if observed_count else "NO_VALID_HIRING_OBSERVATIONS", "observed_theme_count": observed_count, "future_observation_rejected_count": future, "duplicate_observation_count": duplicates, "investment_use_allowed": False, "input_path": str(path.relative_to(root)), "themes": result}


def write_hiring_nowcast(root: Path, output_dir: Path, report: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    report = dict(report)
    report["content_sha256"] = canonical_sha256({key: value for key, value in report.items() if key != "content_sha256"})
    write_json(output_dir / "v3_hiring_nowcast.json", report)
    cache_latest = root / "data_cache/latest"
    cache_latest.mkdir(parents=True, exist_ok=True)
    write_json(cache_latest / "v3_hiring_nowcast.json", report)
