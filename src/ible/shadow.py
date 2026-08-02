from __future__ import annotations

import calendar
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ible.gate_receipt import load_and_verify_gate_receipt
from ible.integrity import canonical_sha256, load_json, write_json
from ible.model_lock import load_and_verify_model_lock
from ible.universe import (
    UniverseError,
    build_universe_status,
    load_and_validate_indicator_contract,
    load_and_validate_universe,
)


class ShadowError(RuntimeError):
    pass


def _parse_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ShadowError(f"invalid {field}: {value!r}") from exc


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _verify_snapshot(payload: dict[str, Any], minimum_theme_count: int) -> dict[str, Any]:
    expected = str(payload.get("content_sha256") or "")
    unsigned = dict(payload)
    unsigned.pop("content_sha256", None)
    actual = canonical_sha256(unsigned)
    if expected != actual:
        raise ShadowError(f"snapshot SHA-256 mismatch: expected={expected or 'missing'} actual={actual}")

    ranking = payload.get("ranking")
    if not isinstance(ranking, list) or len(ranking) < minimum_theme_count:
        raise ShadowError(f"snapshot requires at least {minimum_theme_count} themes")

    ids: set[str] = set()
    prior_score: float | None = None
    for index, row in enumerate(ranking, start=1):
        theme_id = str(row.get("theme_id") or "")
        if not theme_id or theme_id in ids:
            raise ShadowError(f"invalid or duplicate theme_id: {theme_id!r}")
        ids.add(theme_id)
        try:
            score = float(row["boom_score"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ShadowError(f"invalid boom_score for {theme_id}") from exc
        if not 0.0 <= score <= 100.0:
            raise ShadowError(f"boom_score outside 0-100 for {theme_id}")
        if prior_score is not None and score > prior_score:
            raise ShadowError(f"ranking is not descending at row {index}: {theme_id}")
        prior_score = score
    return payload


def load_snapshot(path: Path, minimum_theme_count: int) -> dict[str, Any]:
    try:
        payload = load_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        raise ShadowError(f"snapshot unavailable or invalid: {exc}") from exc
    if not isinstance(payload, dict):
        raise ShadowError("snapshot root must be an object")
    return _verify_snapshot(payload, minimum_theme_count)


def _history_rows(history_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not history_dir.exists():
        return rows
    for path in sorted(history_dir.glob("*/*/*.json")):
        payload = load_json(path)
        expected = str(payload.get("record_sha256") or "")
        unsigned = dict(payload)
        unsigned.pop("record_sha256", None)
        actual = canonical_sha256(unsigned)
        if expected != actual:
            raise ShadowError(f"history record SHA-256 mismatch: {path}")
        try:
            display_path = path.relative_to(history_dir.parent).as_posix()
        except ValueError:
            display_path = path.as_posix()
        rows.append({
            "path": display_path,
            "as_of": payload.get("as_of"),
            "record_sha256": expected,
            "input_sha256": payload.get("input_sha256"),
            "recorded_at": payload.get("recorded_at"),
            "forecast_eligible": payload.get("forecast_eligible"),
            "top_themes": payload.get("top_themes"),
        })
    rows.sort(key=lambda row: (str(row.get("as_of")), str(row.get("path"))))
    return rows


def run_shadow(
    root: Path,
    output_dir: Path,
    run_date: str | None = None,
    history_dir_override: Path | None = None,
) -> dict[str, Any]:
    config = load_json(root / "config/shadow_config.json")
    model_lock = load_and_verify_model_lock(root)
    gate = load_and_verify_gate_receipt(root / "config/v1_1_gate_receipt.json")
    snapshot = load_snapshot(root / str(config["input_file"]), int(config["minimum_theme_count"]))
    try:
        universe = load_and_validate_universe(
            root / str(config["theme_universe_file"]),
            int(config["minimum_universe_count"]),
        )
        indicator_contract = load_and_validate_indicator_contract(root / str(config["indicator_contract_file"]))
        universe_status, data_backlog = build_universe_status(universe, list(snapshot["ranking"]))
    except UniverseError as exc:
        raise ShadowError(str(exc)) from exc

    timezone = ZoneInfo(str(config.get("timezone") or "Asia/Seoul"))
    now = datetime.now(timezone)
    today = _parse_date(run_date, "run_date") if run_date else now.date()
    as_of = _parse_date(str(snapshot.get("as_of") or ""), "snapshot.as_of")
    if as_of > today and not bool((config.get("rules") or {}).get("allow_future_as_of")):
        raise ShadowError(f"snapshot as_of is in the future: {as_of} > {today}")

    input_age_days = (today - as_of).days
    max_age = int(config["max_input_age_days"])
    source_is_bootstrap = str(snapshot.get("source_role") or "").startswith("bootstrap_")
    externally_independent = bool(snapshot.get("externally_independent"))
    new_point_in_time = bool(snapshot.get("new_point_in_time_data"))
    forecast_eligible = bool(not source_is_bootstrap and externally_independent and new_point_in_time and input_age_days <= max_age)

    root_history = history_dir_override or root / str(config["history_dir"])
    existing = _history_rows(root_history)
    previous_hash = existing[-1]["record_sha256"] if existing else None
    target = root_history / f"{as_of.year:04d}" / f"{as_of.month:02d}" / f"{as_of.isoformat()}.json"
    if existing and as_of < _parse_date(str(existing[-1]["as_of"]), "latest history.as_of"):
        raise ShadowError(f"OUT_OF_ORDER_SNAPSHOT: {as_of} is older than latest history {existing[-1]['as_of']}")

    ranking = [dict(row, rank=index) for index, row in enumerate(snapshot["ranking"], start=1)]
    top_themes = [
        {"rank": row["rank"], "theme_id": row["theme_id"], "theme_name": row.get("theme_name"), "boom_score": row["boom_score"]}
        for row in ranking[:5]
    ]

    stale = input_age_days > max_age
    history_action = "NOT_WRITTEN"
    if stale:
        status = "V2_SHADOW_STALE_INPUT_BLOCKED"
        reason = f"입력 기준일이 {input_age_days}일 경과해 허용치 {max_age}일을 초과했습니다. 오래된 점수는 새 예측으로 저장하지 않습니다."
    else:
        if target.exists():
            current = load_json(target)
            expected = str(current.get("record_sha256") or "")
            unsigned_current = dict(current)
            unsigned_current.pop("record_sha256", None)
            if expected != canonical_sha256(unsigned_current):
                raise ShadowError(f"history record SHA-256 mismatch: {target}")
            if (
                current.get("input_sha256") != snapshot.get("content_sha256")
                or current.get("frozen_model_version") != config.get("frozen_model_version")
            ):
                raise ShadowError(f"IMMUTABILITY_VIOLATION: history already exists with different input: {target}")
            history_action = "DUPLICATE_CONFIRMED"
            status = "V2_SHADOW_DUPLICATE_CONFIRMED"
            reason = "동일 기준일과 동일 입력 해시가 이미 봉인되어 있어 기존 기록을 그대로 유지했습니다."
        else:
            record = {
                "schema_version": 1,
                "engine_release": config.get("engine_release"),
                "frozen_model_version": config.get("frozen_model_version"),
                "as_of": as_of.isoformat(),
                "recorded_at": now.isoformat(timespec="seconds"),
                "run_date": today.isoformat(),
                "source_role": snapshot.get("source_role"),
                "externally_independent": externally_independent,
                "new_point_in_time_data": new_point_in_time,
                "forecast_eligible": forecast_eligible,
                "investment_use_allowed": False,
                "input_sha256": snapshot.get("content_sha256"),
                "previous_record_sha256": previous_hash,
                "ranking": ranking,
                "top_themes": top_themes,
                "limitations": [
                    "V0.9.1 계산식은 SHA-256 잠금 상태입니다.",
                    "기준선 입력은 신규 외부 점시점 데이터가 아니므로 실전 예측 적중으로 계산하지 않습니다." if source_is_bootstrap else "외부 독립성과 점시점 여부는 입력 메타데이터와 후속 감사를 거쳐야 합니다.",
                    "6·12·24개월 사후 채점 전까지 투자 사용을 금지합니다.",
                ],
            }
            record["record_sha256"] = canonical_sha256(record)
            write_json(target, record)
            history_action = "CREATED"
            status = "V2_SHADOW_BOOTSTRAP_REGISTERED" if source_is_bootstrap else "V2_SHADOW_SNAPSHOT_RECORDED"
            reason = (
                "V0.9.1 현재 개발진단을 최초 Shadow 기준선으로 봉인했습니다. 이 기준선 자체는 신규 외부 독립검증이 아닙니다."
                if source_is_bootstrap
                else "신규 점시점 입력을 변경 불가능한 Shadow 기록으로 저장했습니다."
            )

    history = _history_rows(root_history)
    ledger = {
        "schema_version": 1,
        "engine_release": config.get("engine_release"),
        "history_count": len(history),
        "latest_as_of": history[-1]["as_of"] if history else None,
        "records": history,
    }
    ledger["ledger_sha256"] = canonical_sha256(ledger)

    queue: list[dict[str, Any]] = []
    for row in history:
        base = _parse_date(str(row["as_of"]), "history.as_of")
        for months in config.get("scorecard_horizons_months", [6, 12, 24]):
            due = _add_months(base, int(months))
            queue.append({
                "snapshot_as_of": base.isoformat(),
                "record_sha256": row["record_sha256"],
                "horizon_months": int(months),
                "due_date": due.isoformat(),
                "status": "DUE" if today >= due else "PENDING",
                "outcome_scored": False,
            })

    summary = {
        "status": status,
        "engine_release": config.get("engine_release"),
        "frozen_model_version": config.get("frozen_model_version"),
        "execution_mode": "github_actions_only",
        "network_collection_used": False,
        "bat_cmd_colab_used": False,
        "investment_use_allowed": False,
        "prospective_shadow_window_started": bool(history),
        "current_snapshot_forecast_eligible": forecast_eligible,
        "external_independence": externally_independent,
        "new_point_in_time_data": new_point_in_time,
        "input_as_of": as_of.isoformat(),
        "input_age_days": input_age_days,
        "max_input_age_days": max_age,
        "history_action": history_action,
        "history_count": len(history),
        "history_target": target.relative_to(root).as_posix() if target.is_relative_to(root) else target.as_posix(),
        "model_lock": model_lock,
        "previous_gate": gate,
        "top_themes": top_themes,
        "reason": reason,
        "theme_universe": universe_status,
        "indicator_contract": {
            "contract_version": indicator_contract.get("contract_version"),
            "required_dimension_count": len(indicator_contract.get("required_dimensions", [])),
            "contract_sha256": indicator_contract.get("contract_sha256"),
        },
        "next_required_gate": "POPULATE_POINT_IN_TIME_DATA_FOR_PENDING_THEMES_THEN_ACCUMULATE_6_12_24_MONTH_OUTCOMES",
    }

    current = {
        "status": status,
        "as_of": as_of.isoformat(),
        "forecast_eligible": forecast_eligible,
        "investment_use_allowed": False,
        "ranking": ranking,
        "input_sha256": snapshot.get("content_sha256"),
        "history_action": history_action,
    }
    next_gate = {
        "current_gate": status,
        "shadow_history_count": len(history),
        "forecast_eligible_snapshot_count": sum(bool(row.get("forecast_eligible")) for row in history),
        "next_required_gate": summary["next_required_gate"],
        "investment_use_allowed": False,
        "theme_universe_count": universe_status["theme_count"],
        "scored_theme_count": universe_status["scored_theme_count"],
        "pending_theme_count": universe_status["pending_theme_count"],
        "score_coverage_percent": universe_status["score_coverage_percent"],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "v2_shadow_summary.json", summary)
    write_json(output_dir / "v2_shadow_current.json", current)
    write_json(output_dir / "v2_shadow_ledger.json", ledger)
    write_json(output_dir / "v2_shadow_scorecard_queue.json", queue)
    write_json(output_dir / "v2_model_lock_verification.json", model_lock)
    write_json(output_dir / "v2_next_gate.json", next_gate)
    write_json(output_dir / "v2_1_theme_universe_status.json", universe_status)
    write_json(output_dir / "v2_1_data_backlog.json", data_backlog)
    write_json(output_dir / "v2_1_indicator_contract.json", indicator_contract)
    return summary
