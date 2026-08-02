from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ible.integrity import canonical_sha256, load_json, write_json
from ible.model_lock import load_and_verify_model_lock
from ible.v3_http import HttpSettings, JsonHttpClient
from ible.v33_engine import _weighted_available
from ible.v40_revenue import (
    aggregate_m3,
    aggregate_qss,
    load_v40_workbooks,
    m3_codes_for_naics,
    parse_m3_series,
    parse_qss_revenue,
    score_growth,
    select_naics_rows,
    source_specific_percentiles,
)


class V40Error(RuntimeError):
    pass


def _theme_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["theme_id"]): row for row in payload.get("themes") or [] if row.get("theme_id")}


def _candidate_stage(score: float | None, direct: float | None, thresholds: dict[str, Any]) -> str:
    if score is None:
        return "INSUFFICIENT_DATA"
    if score >= float(thresholds["candidate_high"]) and (direct or 0) >= float(thresholds["direct_confirmation"]):
        return "PREVALIDATION_HIGH_CANDIDATE"
    if score >= float(thresholds["candidate_watch"]):
        return "PREVALIDATION_WATCH"
    return "MONITOR_ONLY"


def run_v40(root: Path, output_dir: Path, run_date: str | None = None) -> dict[str, Any]:
    config = load_json(root / "config/v40_revenue_sources.json")
    model_lock = load_and_verify_model_lock(root)
    timezone = ZoneInfo(str(config.get("timezone") or "Asia/Seoul"))
    now = datetime.now(timezone)
    as_of = date.fromisoformat(run_date) if run_date else now.date()
    captured_at = now.isoformat(timespec="seconds")

    phase4_path = root / "data_cache/latest/v33_phase4_observations.json"
    mapping_path = root / "config/v31_theme_naics.json"
    if not phase4_path.is_file() or not mapping_path.is_file():
        raise V40Error("required V3.3 cache or theme mapping missing")
    phase4_payload = load_json(phase4_path)
    phase4 = _theme_map(phase4_payload)
    mapping = _theme_map(load_json(mapping_path))
    theme_ids = sorted(phase4)
    if len(theme_ids) != int(config["minimum_theme_count"]):
        raise V40Error(f"theme count mismatch: {len(theme_ids)}")

    client = JsonHttpClient(HttpSettings(timeout_seconds=35, max_attempts=3, base_backoff_seconds=2.0, user_agent="IndustryBoomLeadingEngine/4.0"))
    workbooks = load_v40_workbooks(root, client, config)
    qss_rows = parse_qss_revenue(workbooks["qss"].payload)
    shipments_series = parse_m3_series(workbooks["m3_shipments"].payload)
    orders_series = parse_m3_series(workbooks["m3_new_orders"].payload)
    proxy_map = {str(k): str(v) for k, v in config["m3_proxy_map"].items()}

    raw: dict[str, dict[str, Any]] = {}
    scale_inputs: dict[str, tuple[str, float | None]] = {}
    for theme_id in theme_ids:
        target_codes = [str(code) for code in (mapping.get(theme_id) or {}).get("qcew_naics") or []]
        qss = aggregate_qss(select_naics_rows(qss_rows, target_codes))
        theme_override = (config.get("theme_m3_overrides") or {}).get(theme_id) or {}
        m3_codes = [str(code) for code in theme_override.get("codes") or []]
        if not m3_codes:
            m3_codes = m3_codes_for_naics(target_codes, proxy_map)
        shipments = aggregate_m3(shipments_series, m3_codes, "VS")
        orders = aggregate_m3(orders_series, m3_codes, "NO")
        if qss.get("matched_rows", 0) > 0:
            primary_family = "QSS_REVENUE"
            primary_value = qss.get("current_revenue_million_usd")
            primary_growth = qss.get("revenue_yoy_percent")
            mapping_quality = "DIRECT_QSS_NAICS_REVENUE"
            mapping_note = None
        elif shipments.get("matched_series", 0) > 0:
            primary_family = "M3_SHIPMENTS"
            primary_value = shipments.get("latest_value_million_usd")
            primary_growth = shipments.get("yoy_percent")
            mapping_quality = str(theme_override.get("mapping_quality") or "M3_INDUSTRY_SHIPMENTS_PROXY")
            mapping_note = theme_override.get("note")
        else:
            primary_family = "NO_DIRECT_COVERAGE"
            primary_value = None
            primary_growth = None
            mapping_quality = "NO_DIRECT_REVENUE_COVERAGE"
            mapping_note = None
        raw[theme_id] = {
            "target_naics": target_codes,
            "qss": qss,
            "m3_shipments": shipments,
            "m3_new_orders": orders,
            "primary_family": primary_family,
            "primary_value": primary_value,
            "primary_growth": primary_growth,
            "mapping_quality": mapping_quality,
            "mapping_note": mapping_note,
        }
        scale_inputs[theme_id] = (primary_family, None if primary_value is None else float(primary_value))

    scale_scores = source_specific_percentiles(scale_inputs)
    direct_weights = config["weights"]["direct_commercialization"]
    candidate_weights = config["weights"]["prevalidation_candidate"]
    rows: list[dict[str, Any]] = []
    for theme_id in theme_ids:
        base = phase4[theme_id]
        item = raw[theme_id]
        growth_score = score_growth(item["primary_growth"])
        orders_score = score_growth(item["m3_new_orders"].get("yoy_percent"))
        direct_score = _weighted_available([
            (direct_weights["revenue_or_shipments_growth"], growth_score),
            (direct_weights["revenue_or_shipments_scale"], scale_scores.get(theme_id)),
            (direct_weights["new_orders_growth"], orders_score),
        ])
        phase4_score = base.get("phase4_readiness_signal_score")
        candidate = _weighted_available([
            (candidate_weights["phase4_readiness"], phase4_score),
            (candidate_weights["direct_commercialization"], direct_score),
        ])
        stage = _candidate_stage(candidate, direct_score, config["thresholds"])
        rows.append({
            "theme_id": theme_id,
            "theme_name": base.get("theme_name"),
            "sector": base.get("sector"),
            "status": "V4_DIRECT_COMMERCIALIZATION_OBSERVED" if direct_score is not None else "DIRECT_REVENUE_UNAVAILABLE",
            "candidate_stage": stage,
            "phase4_readiness_signal_score": phase4_score,
            "direct_commercialization_score": direct_score,
            "prevalidation_candidate_score": candidate,
            "boom_score": None,
            "investment_use_allowed": False,
            "mapping_quality": item["mapping_quality"],
            "mapping_note": item.get("mapping_note"),
            "sources": {
                "qss_revenue": item["qss"],
                "m3_shipments": item["m3_shipments"],
                "m3_new_orders": item["m3_new_orders"],
            },
            "components": {
                "primary_growth_percent": item["primary_growth"],
                "primary_growth_score": growth_score,
                "source_specific_scale_percentile": scale_scores.get(theme_id),
                "new_orders_growth_score": orders_score,
            },
            "limitations": [
                "QSS는 서비스 산업 직접 매출이며 M3는 제조업 출하·수주의 산업 프록시입니다.",
                "테마별 상장기업 합산 매출이 아니므로 prevalidation_candidate_score는 최종 boom_score가 아닙니다.",
                "6·12·24개월 미래 성과검증 전에는 투자판단에 사용할 수 없습니다."
            ]
        })

    rows.sort(key=lambda row: (-float(row.get("prevalidation_candidate_score") or -1), str(row["theme_id"])))
    direct_count = sum(1 for row in rows if row["direct_commercialization_score"] is not None)
    qss_count = sum(1 for row in rows if row["mapping_quality"] == "DIRECT_QSS_NAICS_REVENUE")
    m3_count = sum(1 for row in rows if row["mapping_quality"] == "M3_INDUSTRY_SHIPMENTS_PROXY")
    high_count = sum(1 for row in rows if row["candidate_stage"] == "PREVALIDATION_HIGH_CANDIDATE")
    watch_count = sum(1 for row in rows if row["candidate_stage"] == "PREVALIDATION_WATCH")

    observations = {
        "schema_version": 1,
        "engine_release": config["engine_release"],
        "as_of": as_of.isoformat(),
        "captured_at": captured_at,
        "theme_count": len(rows),
        "direct_commercialization_observed_theme_count": direct_count,
        "investment_use_allowed": False,
        "themes": rows,
    }
    observations["content_sha256"] = canonical_sha256(observations)

    ranking = [{
        "rank": index,
        "theme_id": row["theme_id"],
        "theme_name": row["theme_name"],
        "sector": row["sector"],
        "candidate_stage": row["candidate_stage"],
        "prevalidation_candidate_score": row["prevalidation_candidate_score"],
        "direct_commercialization_score": row["direct_commercialization_score"],
        "mapping_quality": row["mapping_quality"],
        "boom_score": None,
        "warning": "직접 매출·출하·수주를 추가한 사전검증 후보점수이며 최종 붐 점수가 아닙니다."
    } for index, row in enumerate(rows, start=1)]

    summary = {
        "status": "V4_0_DIRECT_REVENUE_COMMERCIALIZATION_COLLECTED",
        "engine_release": config["engine_release"],
        "as_of": as_of.isoformat(),
        "theme_count": len(rows),
        "direct_commercialization_observed_theme_count": direct_count,
        "qss_direct_revenue_theme_count": qss_count,
        "m3_shipments_proxy_theme_count": m3_count,
        "high_candidate_count": high_count,
        "watch_candidate_count": watch_count,
        "source_status": {key: {"status": wb.status, "sha256": wb.sha256, "source_error": wb.source_error} for key, wb in workbooks.items()},
        "model_lock": model_lock,
        "engine_build_progress_percent": 96,
        "total_project_progress_percent": 85,
        "automatic_weekly_run_enabled": True,
        "manual_run_required_after_bootstrap": False,
        "investment_use_allowed": False,
        "next_required_gate": "ACCUMULATE_AND_SCORE_6_12_24_MONTH_PROSPECTIVE_OUTCOMES",
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "v40_run_summary.json", summary)
    write_json(output_dir / "v40_direct_commercialization_observations.json", observations)
    write_json(output_dir / "v40_prevalidation_candidate_ranking.json", {"status": "NOT_FINAL_BOOM_SCORE", "as_of": as_of.isoformat(), "ranking": ranking})
    write_json(output_dir / "v40_model_lock_verification.json", model_lock)
    write_json(output_dir / "v40_next_gate.json", {
        "status": "ENGINE_BUILD_COMPLETE_PROSPECTIVE_VALIDATION_REQUIRED",
        "next_required_gate": summary["next_required_gate"],
        "manual_run_required_after_bootstrap": False,
        "investment_use_allowed": False,
    })

    latest = root / "data_cache/latest/v40_direct_commercialization_observations.json"
    dated = root / "data_cache" / f"{as_of.year:04d}" / f"{as_of.month:02d}" / as_of.isoformat() / "v40_direct_commercialization_observations.json"
    write_json(latest, observations)
    write_json(dated, observations)
    history = root / "prospective_history" / f"{as_of.year:04d}" / f"{as_of.month:02d}" / f"{as_of.isoformat()}-v40.json"
    if not history.exists():
        write_json(history, observations)
    return summary
