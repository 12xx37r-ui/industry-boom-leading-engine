from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ible.integrity import canonical_sha256, load_json, write_json
from ible.model_lock import load_and_verify_model_lock
from ible.v50_outcomes import (
    add_months,
    cohort_metrics,
    delta,
    delta_score,
    finite,
    growth_score,
    percent_change,
    weighted_available,
)


class V50Error(RuntimeError):
    pass


def _theme_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["theme_id"]): row for row in payload.get("themes") or [] if row.get("theme_id")}


def _required_json(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    if not path.is_file():
        raise V50Error(f"required input missing: {relative}")
    return load_json(path)


def _primary_metric(row: dict[str, Any]) -> tuple[str | None, float | None, str | None]:
    sources = row.get("sources") or {}
    qss = sources.get("qss_revenue") or {}
    if finite(qss.get("current_revenue_million_usd")) is not None:
        return "QSS_REVENUE", finite(qss.get("current_revenue_million_usd")), str(qss.get("latest_period") or "") or None
    m3 = sources.get("m3_shipments") or {}
    if finite(m3.get("latest_value_million_usd")) is not None:
        return "M3_SHIPMENTS", finite(m3.get("latest_value_million_usd")), str(m3.get("latest_period") or "") or None
    return None, None, None


def _capture_metrics(
    theme_id: str,
    v31: dict[str, dict[str, Any]],
    v32: dict[str, dict[str, Any]],
    v33: dict[str, dict[str, Any]],
    v40: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    p31 = v31.get(theme_id) or {}
    p32 = v32.get(theme_id) or {}
    p33 = v33.get(theme_id) or {}
    p40 = v40.get(theme_id) or {}
    qcew = ((p31.get("sources") or {}).get("qcew") or {})
    recent = qcew.get("recent") or {}
    family, primary_value, primary_period = _primary_metric(p40)
    source_family_count = finite(p33.get("actual_source_family_count"))
    positive_family_count = finite(p33.get("positive_source_family_count"))
    source_diffusion = None
    if source_family_count not in (None, 0.0) and positive_family_count is not None:
        source_diffusion = round(100.0 * positive_family_count / source_family_count, 4)
    return {
        "theme_id": theme_id,
        "theme_name": p40.get("theme_name") or p33.get("theme_name") or p31.get("theme_name"),
        "sector": p40.get("sector") or p33.get("sector") or p31.get("sector"),
        "predicted_score": finite(p40.get("prevalidation_candidate_score")),
        "candidate_stage": p40.get("candidate_stage"),
        "phase4_readiness_score": finite(p40.get("phase4_readiness_signal_score")),
        "direct_commercialization_score": finite(p40.get("direct_commercialization_score")),
        "phase3_investment_score": finite(p32.get("phase3_investment_signal_score")),
        "commercial_metric_family": family,
        "commercial_metric_value_million_usd": primary_value,
        "commercial_metric_period": primary_period,
        "employment": finite(recent.get("employment")),
        "establishments": finite(recent.get("establishments")),
        "total_quarterly_wages": finite(recent.get("total_quarterly_wages")),
        "qcew_quarter": ((p31.get("sources") or {}).get("qcew") or {}).get("quarter"),
        "source_diffusion_percent": source_diffusion,
        "mapping_quality": p40.get("mapping_quality"),
    }


def _build_snapshot(root: Path, as_of: date, captured_at: str, config: dict[str, Any]) -> dict[str, Any]:
    v31_payload = _required_json(root, "data_cache/latest/v31_real_economy_observations.json")
    v32_payload = _required_json(root, "data_cache/latest/v32_corporate_investment_observations.json")
    v33_payload = _required_json(root, "data_cache/latest/v33_phase4_observations.json")
    v40_payload = _required_json(root, "data_cache/latest/v40_direct_commercialization_observations.json")
    v31 = _theme_map(v31_payload)
    v32 = _theme_map(v32_payload)
    v33 = _theme_map(v33_payload)
    v40 = _theme_map(v40_payload)
    theme_ids = sorted(set(v31) | set(v32) | set(v33) | set(v40))
    if len(theme_ids) != int(config["minimum_theme_count"]):
        raise V50Error(f"theme count mismatch: {len(theme_ids)}")
    themes = [_capture_metrics(theme_id, v31, v32, v33, v40) for theme_id in theme_ids]
    themes.sort(key=lambda row: (-float(row.get("predicted_score") or -1), str(row["theme_id"])))
    for rank, row in enumerate(themes, start=1):
        row["predicted_rank"] = rank
    snapshot = {
        "schema_version": 1,
        "engine_release": config["engine_release"],
        "snapshot_id": f"{as_of.year:04d}-{as_of.month:02d}",
        "as_of": as_of.isoformat(),
        "captured_at": captured_at,
        "theme_count": len(themes),
        "source_as_of": {
            "v31": v31_payload.get("as_of"),
            "v32": v32_payload.get("as_of"),
            "v33": v33_payload.get("as_of"),
            "v40": v40_payload.get("as_of"),
        },
        "source_content_sha256": {
            "v31": v31_payload.get("content_sha256"),
            "v32": v32_payload.get("content_sha256"),
            "v33": v33_payload.get("content_sha256"),
            "v40": v40_payload.get("content_sha256"),
        },
        "investment_use_allowed": False,
        "themes": themes,
    }
    snapshot["content_sha256"] = canonical_sha256(snapshot)
    return snapshot


def _load_registry(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schema_version": 1, "snapshots": []}
    payload = load_json(path)
    payload.setdefault("schema_version", 1)
    payload.setdefault("snapshots", [])
    return payload


def _register_snapshot(root: Path, snapshot: dict[str, Any], registry: dict[str, Any], horizons: list[int]) -> tuple[str, dict[str, Any]]:
    snapshot_id = str(snapshot["snapshot_id"])
    snapshot_path = root / "prospective_history/v50_snapshots" / f"{snapshot_id}.json"
    existing = next((item for item in registry["snapshots"] if item.get("snapshot_id") == snapshot_id), None)
    if snapshot_path.is_file():
        stored = load_json(snapshot_path)
        if existing is None:
            registry["snapshots"].append({
                "snapshot_id": snapshot_id,
                "as_of": stored["as_of"],
                "snapshot_sha256": stored["content_sha256"],
                "evaluation_horizons_months": horizons,
                "evaluations": {},
                "status": "AWAITING_FUTURE_OUTCOMES",
            })
        return "REUSED_IMMUTABLE_MONTHLY_SNAPSHOT", stored
    write_json(snapshot_path, snapshot)
    registry["snapshots"].append({
        "snapshot_id": snapshot_id,
        "as_of": snapshot["as_of"],
        "snapshot_sha256": snapshot["content_sha256"],
        "evaluation_horizons_months": horizons,
        "evaluations": {},
        "status": "AWAITING_FUTURE_OUTCOMES",
    })
    registry["snapshots"].sort(key=lambda item: str(item["snapshot_id"]))
    return "CREATED_IMMUTABLE_MONTHLY_SNAPSHOT", snapshot


def _evaluate_theme(baseline: dict[str, Any], current: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    same_family = baseline.get("commercial_metric_family") and baseline.get("commercial_metric_family") == current.get("commercial_metric_family")
    commercial_growth = percent_change(current.get("commercial_metric_value_million_usd"), baseline.get("commercial_metric_value_million_usd")) if same_family else None
    direct_delta = delta(current.get("direct_commercialization_score"), baseline.get("direct_commercialization_score"))
    phase4_delta = delta(current.get("phase4_readiness_score"), baseline.get("phase4_readiness_score"))
    employment_growth = percent_change(current.get("employment"), baseline.get("employment"))
    establishment_growth = percent_change(current.get("establishments"), baseline.get("establishments"))
    wage_growth = percent_change(current.get("total_quarterly_wages"), baseline.get("total_quarterly_wages"))
    diffusion_delta = delta(current.get("source_diffusion_percent"), baseline.get("source_diffusion_percent"))
    transforms = config["score_transforms"]
    component_scores = {
        "commercial_level_growth": growth_score(commercial_growth, transforms),
        "direct_commercialization_score_change": delta_score(direct_delta, transforms),
        "phase4_readiness_score_change": delta_score(phase4_delta, transforms),
        "employment_level_growth": growth_score(employment_growth, transforms),
        "establishment_level_growth": growth_score(establishment_growth, transforms),
        "wage_level_growth": growth_score(wage_growth, transforms),
        "source_diffusion_change": delta_score(diffusion_delta, transforms),
    }
    weights = config["outcome_weights"]
    outcome = weighted_available([(float(weights[key]), component_scores.get(key)) for key in weights])
    threshold = float(config["success_thresholds"]["realized_outcome_score"])
    minimum_growth = float(config["success_thresholds"]["minimum_commercial_level_growth_percent"])
    growth_condition = commercial_growth is None or commercial_growth >= minimum_growth
    success = outcome is not None and outcome >= threshold and growth_condition
    return {
        "theme_id": baseline["theme_id"],
        "theme_name": baseline.get("theme_name"),
        "sector": baseline.get("sector"),
        "predicted_rank": baseline.get("predicted_rank"),
        "predicted_score": baseline.get("predicted_score"),
        "candidate_stage_at_prediction": baseline.get("candidate_stage"),
        "realized_outcome_score": outcome,
        "realized_success": success,
        "commercial_metric_comparable": bool(same_family),
        "changes": {
            "commercial_level_growth_percent": commercial_growth,
            "direct_commercialization_score_delta": direct_delta,
            "phase4_readiness_score_delta": phase4_delta,
            "employment_level_growth_percent": employment_growth,
            "establishment_level_growth_percent": establishment_growth,
            "wage_level_growth_percent": wage_growth,
            "source_diffusion_delta": diffusion_delta,
        },
        "component_scores": component_scores,
        "baseline_metric_period": baseline.get("commercial_metric_period"),
        "current_metric_period": current.get("commercial_metric_period"),
        "mapping_quality": current.get("mapping_quality"),
    }


def _evaluate_snapshot(root: Path, registry_item: dict[str, Any], current_snapshot: dict[str, Any], horizon: int, config: dict[str, Any]) -> dict[str, Any]:
    snapshot_id = str(registry_item["snapshot_id"])
    baseline = load_json(root / "prospective_history/v50_snapshots" / f"{snapshot_id}.json")
    baseline_map = _theme_map(baseline)
    current_map = _theme_map(current_snapshot)
    rows = [_evaluate_theme(baseline_map[theme_id], current_map[theme_id], config) for theme_id in sorted(set(baseline_map) & set(current_map))]
    rows.sort(key=lambda row: (int(row.get("predicted_rank") or 10_000), str(row["theme_id"])))
    cohort_sizes = [int(value) for value in config.get("candidate_cohort_sizes") or [10]]
    metrics = {f"top_{size}": cohort_metrics(rows, size) for size in cohort_sizes}
    evaluation = {
        "schema_version": 1,
        "engine_release": config["engine_release"],
        "snapshot_id": snapshot_id,
        "baseline_as_of": baseline["as_of"],
        "evaluated_as_of": current_snapshot["as_of"],
        "horizon_months": int(horizon),
        "theme_count": len(rows),
        "cohort_metrics": metrics,
        "investment_use_allowed": False,
        "themes": rows,
    }
    evaluation["content_sha256"] = canonical_sha256(evaluation)
    return evaluation


def _aggregate_evaluations(root: Path, registry: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    by_horizon: dict[int, list[dict[str, Any]]] = {int(h): [] for h in config["evaluation_horizons_months"]}
    for item in registry.get("snapshots") or []:
        for raw_horizon, relative in (item.get("evaluations") or {}).items():
            path = root / str(relative)
            if path.is_file():
                by_horizon.setdefault(int(raw_horizon), []).append(load_json(path))
    horizon_metrics: dict[str, Any] = {}
    for horizon, evaluations in sorted(by_horizon.items()):
        rows = [row for evaluation in evaluations for row in evaluation.get("themes") or []]
        horizon_metrics[str(horizon)] = {
            "matured_snapshot_count": len(evaluations),
            "theme_observation_count": len(rows),
            "top_10": cohort_metrics(rows, 10),
        }
    gate = config["investment_gate"]
    checks: list[dict[str, Any]] = []
    for horizon in config["evaluation_horizons_months"]:
        metrics = horizon_metrics[str(horizon)]
        required = int(gate["minimum_matured_snapshots"][str(horizon)])
        checks.append({"check": f"{horizon}m_matured_snapshots", "passed": metrics["matured_snapshot_count"] >= required, "actual": metrics["matured_snapshot_count"], "required": required})
        top = metrics["top_10"]
        checks.append({"check": f"{horizon}m_top10_success_rate", "passed": top["top_success_rate"] is not None and top["top_success_rate"] >= float(gate["minimum_top10_success_rate"]), "actual": top["top_success_rate"], "required": gate["minimum_top10_success_rate"]})
        checks.append({"check": f"{horizon}m_top_bottom_spread", "passed": top["top_bottom_outcome_spread"] is not None and top["top_bottom_outcome_spread"] >= float(gate["minimum_top_bottom_outcome_spread"]), "actual": top["top_bottom_outcome_spread"], "required": gate["minimum_top_bottom_outcome_spread"]})
        checks.append({"check": f"{horizon}m_rank_correlation", "passed": top["rank_correlation"] is not None and top["rank_correlation"] >= float(gate["minimum_rank_correlation"]), "actual": top["rank_correlation"], "required": gate["minimum_rank_correlation"]})
    allowed = bool(checks) and all(check["passed"] for check in checks)
    return {
        "status": "PROSPECTIVE_VALIDATION_PASSED" if allowed else "PROSPECTIVE_VALIDATION_ACCUMULATING",
        "horizon_metrics": horizon_metrics,
        "gate_checks": checks,
        "investment_use_allowed": allowed,
    }


def run_v50(root: Path, output_dir: Path, run_date: str | None = None) -> dict[str, Any]:
    config = _required_json(root, "config/v50_prospective_validation.json")
    model_lock = load_and_verify_model_lock(root)
    timezone = ZoneInfo(str(config.get("timezone") or "Asia/Seoul"))
    now = datetime.now(timezone)
    as_of = date.fromisoformat(run_date) if run_date else now.date()
    captured_at = now.isoformat(timespec="seconds")
    horizons = [int(value) for value in config["evaluation_horizons_months"]]

    current_snapshot = _build_snapshot(root, as_of, captured_at, config)
    registry_path = root / "prospective_history/v50_snapshot_registry.json"
    registry = _load_registry(registry_path)
    snapshot_action, registered_snapshot = _register_snapshot(root, current_snapshot, registry, horizons)

    current_map = _theme_map(current_snapshot)
    matured_now = 0
    for item in registry.get("snapshots") or []:
        baseline_date = date.fromisoformat(str(item["as_of"]))
        evaluations = item.setdefault("evaluations", {})
        for horizon in horizons:
            key = str(horizon)
            due = add_months(baseline_date, horizon)
            if key in evaluations or as_of < due:
                continue
            evaluation = _evaluate_snapshot(root, item, current_snapshot, horizon, config)
            relative = f"prospective_history/v50_evaluations/{item['snapshot_id']}-h{horizon:02d}.json"
            write_json(root / relative, evaluation)
            evaluations[key] = relative
            matured_now += 1
        completed = len(evaluations)
        item["status"] = "ALL_HORIZONS_EVALUATED" if completed == len(horizons) else ("PARTIALLY_EVALUATED" if completed else "AWAITING_FUTURE_OUTCOMES")
    registry["content_sha256"] = canonical_sha256({"schema_version": registry.get("schema_version", 1), "snapshots": registry.get("snapshots", [])})
    write_json(registry_path, registry)

    scorecard = _aggregate_evaluations(root, registry, config)
    ranking = [{
        "rank": row["predicted_rank"],
        "theme_id": row["theme_id"],
        "theme_name": row["theme_name"],
        "sector": row["sector"],
        "candidate_stage": row["candidate_stage"],
        "prevalidation_candidate_score": row["predicted_score"],
        "direct_commercialization_score": row["direct_commercialization_score"],
        "mapping_quality": row["mapping_quality"],
        "boom_score": None,
    } for row in current_snapshot["themes"]]

    next_due_dates = []
    for item in registry.get("snapshots") or []:
        baseline_date = date.fromisoformat(str(item["as_of"]))
        for horizon in horizons:
            if str(horizon) not in (item.get("evaluations") or {}):
                next_due_dates.append({"snapshot_id": item["snapshot_id"], "horizon_months": horizon, "due_date": add_months(baseline_date, horizon).isoformat()})
    next_due_dates.sort(key=lambda row: (row["due_date"], row["snapshot_id"], row["horizon_months"]))

    total_evaluations = sum(len(item.get("evaluations") or {}) for item in registry.get("snapshots") or [])
    progress = config["progress"]
    summary = {
        "status": "V5_0_PROSPECTIVE_VALIDATION_ACTIVE",
        "engine_release": config["engine_release"],
        "as_of": as_of.isoformat(),
        "theme_count": len(current_snapshot["themes"]),
        "commercialization_coverage_theme_count": sum(1 for row in current_snapshot["themes"] if row.get("direct_commercialization_score") is not None),
        "monthly_snapshot_action": snapshot_action,
        "monthly_snapshot_count": len(registry.get("snapshots") or []),
        "evaluations_created_this_run": matured_now,
        "total_matured_evaluation_count": total_evaluations,
        "next_evaluation_due": next_due_dates[0] if next_due_dates else None,
        "model_lock": model_lock,
        "engine_build_progress_percent": progress["engine_build_percent_after_release"],
        "total_project_progress_percent": progress["total_project_percent_before_outcomes"],
        "automatic_weekly_run_enabled": True,
        "manual_run_required_after_bootstrap": False,
        "investment_use_allowed": scorecard["investment_use_allowed"],
        "next_required_gate": "WAIT_FOR_AND_AUTOMATICALLY_SCORE_6_12_24_MONTH_OUTCOMES",
        "remaining": progress["remaining"],
    }

    dashboard = {
        "status": summary["status"],
        "as_of": summary["as_of"],
        "progress": {
            "engine_build_percent": summary["engine_build_progress_percent"],
            "total_project_percent": summary["total_project_progress_percent"],
            "snapshot_count": summary["monthly_snapshot_count"],
            "matured_evaluation_count": summary["total_matured_evaluation_count"],
            "next_evaluation_due": summary["next_evaluation_due"],
        },
        "investment_use_allowed": summary["investment_use_allowed"],
        "ranking": ranking,
        "validation": scorecard,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "v50_run_summary.json", summary)
    # Keep the immutable monthly baseline exactly as before for prospective validation,
    # while also exposing this run's freshly built snapshot for downstream operational use.
    write_json(output_dir / "v50_current_monthly_snapshot.json", registered_snapshot)
    write_json(output_dir / "v50_current_run_snapshot.json", current_snapshot)
    write_json(output_dir / "v50_candidate_ranking.json", {"status": "PREVALIDATION_ONLY", "as_of": as_of.isoformat(), "ranking": ranking})
    write_json(output_dir / "v50_prospective_scorecard.json", scorecard)
    write_json(output_dir / "v50_snapshot_registry.json", registry)
    write_json(output_dir / "v50_dashboard_payload.json", dashboard)
    write_json(output_dir / "v50_model_lock_verification.json", model_lock)
    write_json(output_dir / "v50_next_gate.json", {
        "status": "ENGINE_CODE_COMPLETE_FUTURE_OUTCOMES_PENDING",
        "next_required_gate": summary["next_required_gate"],
        "next_evaluation_due": summary["next_evaluation_due"],
        "manual_run_required_after_bootstrap": False,
        "investment_use_allowed": summary["investment_use_allowed"],
    })
    return summary
