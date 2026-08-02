from __future__ import annotations

import json
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ible.integrity import canonical_sha256, load_json, write_json
from ible.model_lock import load_and_verify_model_lock
from ible.v32_investment import bounded_growth_score, percentile_scores


class V33Error(RuntimeError):
    pass


def _theme_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("theme_id")): row
        for row in payload.get("themes") or []
        if row.get("theme_id")
    }


def _load_required(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    if not path.is_file():
        raise V33Error(f"required point-in-time cache missing: {relative}")
    try:
        return load_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        raise V33Error(f"invalid point-in-time cache: {relative}: {exc}") from exc


def _weighted_available(parts: list[tuple[float, float | None]]) -> float | None:
    observed = [(float(weight), float(value)) for weight, value in parts if value is not None]
    if not observed:
        return None
    total_weight = sum(weight for weight, _ in observed)
    if total_weight <= 0:
        return None
    return round(sum(weight * value for weight, value in observed) / total_weight, 4)


def _growth_percent_score(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return bounded_growth_score(float(value) / 100.0)
    except (TypeError, ValueError):
        return None


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _source_signals(phase1: dict[str, Any], phase2: dict[str, Any], phase3: dict[str, Any]) -> dict[str, float | None]:
    sources1 = phase1.get("sources") or {}
    sources2 = phase2.get("sources") or {}
    sources3 = phase3.get("sources") or {}
    return {
        "research": _finite((sources1.get("openalex") or {}).get("source_signal_score")),
        "government_spending": _finite((sources1.get("usaspending") or {}).get("source_signal_score")),
        "real_economy": _finite((sources2.get("qcew") or {}).get("source_signal_score")),
        "capex": _finite((sources3.get("aies_capex") or {}).get("source_signal_score")),
        "business_rd": _finite((sources3.get("berd_business_rd") or {}).get("source_signal_score")),
    }


def _readiness_stage(score: float | None, commercialization: float | None, supply_chain: float | None, thresholds: dict[str, Any]) -> str:
    if score is None:
        return "INSUFFICIENT_DATA"
    if score >= float(thresholds["high_readiness"]) and (commercialization or 0) >= 55 and (supply_chain or 0) >= 50:
        return "HIGH_COMMERCIALIZATION_READINESS"
    if score >= float(thresholds["watch_readiness"]):
        return "EARLY_ACCUMULATION_WATCH"
    return "MONITOR_ONLY"


def run_v33(root: Path, output_dir: Path, run_date: str | None = None) -> dict[str, Any]:
    config = load_json(root / "config/v33_auto_engine.json")
    model_lock = load_and_verify_model_lock(root)
    timezone = ZoneInfo(str(config.get("timezone") or "Asia/Seoul"))
    now = datetime.now(timezone)
    as_of = date.fromisoformat(run_date) if run_date else now.date()
    captured_at = now.isoformat(timespec="seconds")

    phase1_payload = _load_required(root, "data_cache/latest/v3_source_observations.json")
    phase2_payload = _load_required(root, "data_cache/latest/v31_real_economy_observations.json")
    phase3_payload = _load_required(root, "data_cache/latest/v32_corporate_investment_observations.json")
    phase1 = _theme_map(phase1_payload)
    phase2 = _theme_map(phase2_payload)
    phase3 = _theme_map(phase3_payload)
    theme_ids = sorted(set(phase1) | set(phase2) | set(phase3))
    if len(theme_ids) != int(config["minimum_theme_count"]):
        raise V33Error(f"theme count mismatch: {len(theme_ids)}")

    breadth_raw: dict[str, float | None] = {}
    for theme_id in theme_ids:
        qcew = ((phase2.get(theme_id) or {}).get("sources") or {}).get("qcew") or {}
        recent = qcew.get("recent") or {}
        breadth_raw[theme_id] = _finite(recent.get("matched_naics_count"))
    breadth_percentile = percentile_scores(breadth_raw)

    rows: list[dict[str, Any]] = []
    commercial_weights = config["weights"]["commercialization"]
    supply_weights = config["weights"]["supply_chain_proxy"]
    phase4_weights = config["weights"]["phase4"]
    positive_cutoff = float(config["thresholds"]["positive_source_signal"])

    for theme_id in theme_ids:
        p1 = phase1.get(theme_id) or {}
        p2 = phase2.get(theme_id) or {}
        p3 = phase3.get(theme_id) or {}
        base = p3 or p2 or p1
        qcew = ((p2.get("sources") or {}).get("qcew") or {})
        signals = _source_signals(p1, p2, p3)

        employment_growth_score = _growth_percent_score(qcew.get("employment_growth_percent"))
        establishment_growth_score = _growth_percent_score(qcew.get("establishment_growth_percent"))
        wage_growth_score = _growth_percent_score(qcew.get("wage_growth_percent"))
        capex_score = signals["capex"]
        rd_score = signals["business_rd"]
        commercialization = _weighted_available([
            (commercial_weights["employment_growth"], employment_growth_score),
            (commercial_weights["establishment_growth"], establishment_growth_score),
            (commercial_weights["wage_growth"], wage_growth_score),
            (commercial_weights["capex"], capex_score),
            (commercial_weights["business_rd"], rd_score),
        ])

        observed_signals = [value for value in signals.values() if value is not None]
        positive_signals = [value for value in observed_signals if value >= positive_cutoff]
        diffusion = None if not observed_signals else round(100.0 * len(positive_signals) / len(observed_signals), 4)
        breadth = breadth_percentile.get(theme_id)
        supply_chain = _weighted_available([
            (supply_weights["naics_breadth"], breadth),
            (supply_weights["cross_source_diffusion"], diffusion),
        ])

        phase3_score = _finite(p3.get("phase3_investment_signal_score"))
        phase4 = _weighted_available([
            (phase4_weights["phase3"], phase3_score),
            (phase4_weights["commercialization_proxy"], commercialization),
            (phase4_weights["supply_chain_diffusion_proxy"], supply_chain),
        ])
        stage = _readiness_stage(phase4, commercialization, supply_chain, config["thresholds"])

        rows.append({
            "theme_id": theme_id,
            "theme_name": base.get("theme_name"),
            "sector": base.get("sector"),
            "data_build_priority": base.get("data_build_priority"),
            "status": "PHASE4_PROXY_OBSERVED" if phase4 is not None else "INSUFFICIENT_DATA",
            "readiness_stage": stage,
            "phase3_investment_signal_score": phase3_score,
            "commercialization_proxy_score": commercialization,
            "supply_chain_diffusion_proxy_score": supply_chain,
            "phase4_readiness_signal_score": phase4,
            "boom_score": None,
            "frozen_model_score_eligible": False,
            "actual_source_family_count": len(observed_signals),
            "positive_source_family_count": len(positive_signals),
            "components": {
                "employment_growth_score": employment_growth_score,
                "establishment_growth_score": establishment_growth_score,
                "wage_growth_score": wage_growth_score,
                "capex_signal_score": capex_score,
                "business_rd_signal_score": rd_score,
                "naics_breadth_percentile_score": breadth,
                "cross_source_diffusion_score": diffusion,
                "source_signals": signals,
            },
            "limitations": [
                "commercialization_proxy_score는 실제 기업 매출이 아니라 고용·사업체·임금·CAPEX·R&D의 사업화 프록시입니다.",
                "supply_chain_diffusion_proxy_score는 BEA 투입산출표가 아니라 NAICS 폭과 다원천 확산의 프록시입니다.",
                "phase4_readiness_signal_score는 최종 boom_score가 아니며 투자판단에 사용할 수 없습니다.",
            ],
        })

    rows.sort(key=lambda row: (-float(row["phase4_readiness_signal_score"] or -1), str(row["theme_id"])))
    observations = {
        "schema_version": 1,
        "engine_release": config["engine_release"],
        "as_of": as_of.isoformat(),
        "captured_at": captured_at,
        "theme_count": len(rows),
        "source_cache_as_of": {
            "phase1": phase1_payload.get("as_of"),
            "phase2": phase2_payload.get("as_of"),
            "phase3": phase3_payload.get("as_of"),
        },
        "investment_use_allowed": False,
        "themes": rows,
    }
    observations["content_sha256"] = canonical_sha256(observations)

    ranking = [{
        "rank": index,
        "theme_id": row["theme_id"],
        "theme_name": row["theme_name"],
        "sector": row["sector"],
        "readiness_stage": row["readiness_stage"],
        "phase4_readiness_signal_score": row["phase4_readiness_signal_score"],
        "commercialization_proxy_score": row["commercialization_proxy_score"],
        "supply_chain_diffusion_proxy_score": row["supply_chain_diffusion_proxy_score"],
        "boom_score": None,
        "warning": "사업화·공급망 프록시 통합순위이며 최종 산업 붐 점수가 아닙니다.",
    } for index, row in enumerate(rows, start=1)]

    high_count = sum(1 for row in rows if row["readiness_stage"] == "HIGH_COMMERCIALIZATION_READINESS")
    watch_count = sum(1 for row in rows if row["readiness_stage"] == "EARLY_ACCUMULATION_WATCH")
    summary = {
        "status": "V3_3_AUTO_ENGINE_BOOTSTRAPPED",
        "engine_release": config["engine_release"],
        "as_of": as_of.isoformat(),
        "theme_count": len(rows),
        "phase4_observed_theme_count": sum(1 for row in rows if row["phase4_readiness_signal_score"] is not None),
        "high_readiness_theme_count": high_count,
        "watch_theme_count": watch_count,
        "model_lock": model_lock,
        "engine_build_progress_percent": config["progress"]["engine_build_percent_after_release"],
        "total_project_progress_percent": config["progress"]["total_project_percent_after_release"],
        "automatic_weekly_run_enabled": True,
        "manual_run_required_after_bootstrap": False,
        "frozen_boom_score_new_theme_count": 0,
        "investment_use_allowed": False,
        "next_required_gate": "ACCUMULATE_NEW_POINT_IN_TIME_OUTCOMES_AT_6_12_24_MONTHS_AND_ADD_DIRECT_REVENUE_SOURCE",
        "remaining": config["progress"]["remaining"],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "v33_run_summary.json", summary)
    write_json(output_dir / "v33_phase4_observations.json", observations)
    write_json(output_dir / "v33_phase4_readiness_ranking.json", {
        "status": "NOT_A_FINAL_BOOM_SCORE_RANKING",
        "as_of": as_of.isoformat(),
        "ranking": ranking,
    })
    write_json(output_dir / "v33_model_lock_verification.json", model_lock)
    write_json(output_dir / "v33_next_gate.json", {
        "status": "ENGINE_BUILD_NEAR_COMPLETE_PROSPECTIVE_VALIDATION_RUNNING",
        "next_required_gate": summary["next_required_gate"],
        "manual_run_required_after_bootstrap": False,
        "investment_use_allowed": False,
    })

    latest = root / "data_cache/latest/v33_phase4_observations.json"
    dated = root / "data_cache" / f"{as_of.year:04d}" / f"{as_of.month:02d}" / as_of.isoformat() / "v33_phase4_observations.json"
    write_json(latest, observations)
    write_json(dated, observations)

    history = root / "prospective_history" / f"{as_of.year:04d}" / f"{as_of.month:02d}" / f"{as_of.isoformat()}.json"
    if not history.exists():
        write_json(history, observations)
    queue_path = root / "prospective_history/prospective_evaluation_queue.json"
    queue = load_json(queue_path) if queue_path.is_file() else {"schema_version": 1, "snapshots": []}
    if not any(item.get("as_of") == as_of.isoformat() for item in queue.get("snapshots") or []):
        queue.setdefault("snapshots", []).append({
            "as_of": as_of.isoformat(),
            "observation_sha256": observations["content_sha256"],
            "evaluation_horizons_months": [6, 12, 24],
            "status": "AWAITING_FUTURE_OUTCOMES",
        })
        write_json(queue_path, queue)
    return summary
