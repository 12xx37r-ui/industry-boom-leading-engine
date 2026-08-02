from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from ible.integrity import canonical_sha256, load_json


class UniverseError(RuntimeError):
    pass


def load_and_validate_universe(path: Path, minimum_count: int) -> dict[str, Any]:
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise UniverseError("theme universe root must be an object")
    rows = payload.get("themes")
    if not isinstance(rows, list) or len(rows) < minimum_count:
        raise UniverseError(f"theme universe requires at least {minimum_count} themes")

    ids: set[str] = set()
    names: set[str] = set()
    allowed_status = {"SCORING_ACTIVE", "DATA_PIPELINE_PENDING"}
    for row in rows:
        if not isinstance(row, dict):
            raise UniverseError("theme universe row must be an object")
        theme_id = str(row.get("theme_id") or "").strip()
        theme_name = str(row.get("theme_name") or "").strip()
        sector = str(row.get("sector") or "").strip()
        status = str(row.get("release_status") or "").strip()
        try:
            priority = int(row.get("data_build_priority"))
        except (TypeError, ValueError) as exc:
            raise UniverseError(f"invalid data_build_priority for {theme_id}") from exc
        if not theme_id or theme_id in ids:
            raise UniverseError(f"invalid or duplicate theme_id: {theme_id!r}")
        if not theme_name or theme_name in names:
            raise UniverseError(f"invalid or duplicate theme_name: {theme_name!r}")
        if not sector:
            raise UniverseError(f"missing sector for {theme_id}")
        if status not in allowed_status:
            raise UniverseError(f"invalid release_status for {theme_id}: {status}")
        if priority not in {1, 2, 3}:
            raise UniverseError(f"priority must be 1-3 for {theme_id}")
        ids.add(theme_id)
        names.add(theme_name)

    payload = dict(payload)
    payload["theme_count"] = len(rows)
    payload["universe_sha256"] = canonical_sha256(payload)
    return payload


def load_and_validate_indicator_contract(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    rows = payload.get("required_dimensions") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) != 8:
        raise UniverseError("indicator contract must define exactly 8 dimensions")
    ids = [str(row.get("dimension_id") or "") for row in rows]
    if any(not value for value in ids) or len(ids) != len(set(ids)):
        raise UniverseError("indicator dimension ids must be unique and non-empty")
    if not all(bool(row.get("required")) for row in rows):
        raise UniverseError("all 8 dimensions must remain required in V2.1")
    result = dict(payload)
    result["contract_sha256"] = canonical_sha256(payload)
    return result


def build_universe_status(universe: dict[str, Any], ranking: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    universe_rows = list(universe["themes"])
    by_id = {str(row["theme_id"]): row for row in universe_rows}
    ranking_ids = [str(row.get("theme_id") or "") for row in ranking]
    unknown = sorted(set(ranking_ids) - set(by_id))
    if unknown:
        raise UniverseError(f"snapshot contains themes outside universe: {unknown}")

    scored_ids = set(ranking_ids)
    pending = [row for row in universe_rows if str(row["theme_id"]) not in scored_ids]
    active = [row for row in universe_rows if str(row["theme_id"]) in scored_ids]
    total = len(universe_rows)
    scored = len(active)
    coverage = round(scored / total, 4) if total else 0.0
    sectors = Counter(str(row["sector"]) for row in universe_rows)
    pending_by_sector = Counter(str(row["sector"]) for row in pending)

    status = {
        "status": "V2_1_UNIVERSE_EXPANDED_DATA_PENDING" if pending else "V2_1_UNIVERSE_FULLY_SCORED",
        "universe_version": universe.get("universe_version"),
        "theme_count": total,
        "scored_theme_count": scored,
        "pending_theme_count": len(pending),
        "score_coverage_ratio": coverage,
        "score_coverage_percent": round(coverage * 100, 1),
        "fabricated_score_count": 0,
        "sectors": dict(sorted(sectors.items())),
        "pending_by_sector": dict(sorted(pending_by_sector.items())),
        "active_themes": [
            {
                "theme_id": row["theme_id"],
                "theme_name": row["theme_name"],
                "sector": row["sector"],
            }
            for row in active
        ],
        "universe_sha256": universe.get("universe_sha256"),
    }

    backlog_rows = sorted(
        (
            {
                "theme_id": row["theme_id"],
                "theme_name": row["theme_name"],
                "sector": row["sector"],
                "data_build_priority": row["data_build_priority"],
                "status": "POINT_IN_TIME_DATA_REQUIRED",
                "missing_dimensions": 8,
            }
            for row in pending
        ),
        key=lambda row: (int(row["data_build_priority"]), str(row["sector"]), str(row["theme_name"])),
    )
    backlog = {
        "status": "V2_1_DATA_BACKLOG_CREATED",
        "pending_theme_count": len(backlog_rows),
        "priority_1_count": sum(int(row["data_build_priority"]) == 1 for row in backlog_rows),
        "priority_2_count": sum(int(row["data_build_priority"]) == 2 for row in backlog_rows),
        "priority_3_count": sum(int(row["data_build_priority"]) == 3 for row in backlog_rows),
        "rule": "자료가 없는 산업에는 점수를 만들지 않고 8개 필수 축이 채워질 때까지 대기시킵니다.",
        "themes": backlog_rows,
    }
    return status, backlog
