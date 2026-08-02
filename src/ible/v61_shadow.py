from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from ible.integrity import canonical_sha256, file_sha256, load_json, write_json


class V61Error(RuntimeError):
    pass


def _safe_div(numerator: int | float, denominator: int | float) -> float | None:
    return round(float(numerator) / float(denominator), 4) if denominator else None


def verify_policy_lock(root: Path) -> dict[str, Any]:
    lock_path = root / "config/v61_policy_lock.json"
    lock = load_json(lock_path)
    policy_path = root / str(lock["policy_file"])
    actual = file_sha256(policy_path) if policy_path.is_file() else None
    status = "POLICY_LOCK_VERIFIED" if actual == lock.get("expected_sha256") else "POLICY_LOCK_FAILED"
    result = {
        "status": status,
        "policy_id": lock.get("policy_id"),
        "policy_file": lock.get("policy_file"),
        "expected_sha256": lock.get("expected_sha256"),
        "actual_sha256": actual,
        "sealed_at": lock.get("sealed_at"),
    }
    if status != "POLICY_LOCK_VERIFIED":
        raise V61Error(f"V6.1 policy lock failed: {result}")
    return result


def _champion_alert(row: dict[str, Any]) -> bool:
    return str(row.get("candidate_stage") or "") == "PREVALIDATION_HIGH_CANDIDATE"


def _challenger_alert(row: dict[str, Any], policy: dict[str, Any]) -> bool:
    if _champion_alert(row):
        return True
    bridge = policy["challenger_live_policy"]["watch_bridge"]
    if str(row.get("candidate_stage") or "") != str(bridge["required_stage"]):
        return False
    checks = [
        int(row.get("predicted_rank") or 9999) <= int(bridge["rank_max"]),
        float(row.get("predicted_score") or -1) >= float(bridge["predicted_score_min"]),
        float(row.get("direct_commercialization_score") or -1) >= float(bridge["direct_commercialization_score_min"]),
        float(row.get("phase3_investment_score") or -1) >= float(bridge["phase3_investment_score_min"]),
        float(row.get("source_diffusion_percent") or -1) >= float(bridge["source_diffusion_percent_min"]),
    ]
    return all(checks)


def _decision_rows(snapshot: dict[str, Any], policy: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in snapshot.get("themes") or []:
        champion = _champion_alert(source)
        challenger = _challenger_alert(source, policy)
        rows.append({
            "rank": source.get("predicted_rank"),
            "theme_id": source.get("theme_id"),
            "theme_name": source.get("theme_name"),
            "sector": source.get("sector"),
            "candidate_stage": source.get("candidate_stage"),
            "predicted_score": source.get("predicted_score"),
            "direct_commercialization_score": source.get("direct_commercialization_score"),
            "phase3_investment_score": source.get("phase3_investment_score"),
            "source_diffusion_percent": source.get("source_diffusion_percent"),
            "champion_live_alert": champion,
            "challenger_live_alert": challenger,
            "added_by_challenger": challenger and not champion,
            "boom_score": None,
        })
    rows.sort(key=lambda row: (int(row.get("rank") or 9999), str(row.get("theme_id") or "")))
    return rows


def _load_registry(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schema_version": 1, "snapshots": []}
    payload = load_json(path)
    payload.setdefault("schema_version", 1)
    payload.setdefault("snapshots", [])
    return payload


def _register_snapshot(root: Path, snapshot: dict[str, Any], registry: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    snapshot_id = str(snapshot["snapshot_id"])
    path = root / "prospective_history/v61_policy_snapshots" / f"{snapshot_id}.json"
    existing_registry = next((row for row in registry["snapshots"] if row.get("snapshot_id") == snapshot_id), None)
    if path.is_file():
        stored = load_json(path)
        if stored.get("content_sha256") != snapshot.get("content_sha256"):
            raise V61Error(f"immutable V6.1 snapshot mismatch: {snapshot_id}")
        if existing_registry is None:
            registry["snapshots"].append({
                "snapshot_id": snapshot_id,
                "as_of": stored["as_of"],
                "snapshot_sha256": stored["content_sha256"],
                "status": "AWAITING_FUTURE_OUTCOMES",
            })
        return "REUSED_IMMUTABLE_POLICY_SNAPSHOT", stored
    write_json(path, snapshot)
    registry["snapshots"].append({
        "snapshot_id": snapshot_id,
        "as_of": snapshot["as_of"],
        "snapshot_sha256": snapshot["content_sha256"],
        "status": "AWAITING_FUTURE_OUTCOMES",
    })
    registry["snapshots"].sort(key=lambda row: str(row["snapshot_id"]))
    return "CREATED_IMMUTABLE_POLICY_SNAPSHOT", snapshot


def _policy_metrics(rows: list[dict[str, Any]], decision_key: str) -> dict[str, Any]:
    alerted = [row for row in rows if bool(row.get(decision_key))]
    non_alerted = [row for row in rows if not bool(row.get(decision_key))]
    successes = [row for row in rows if bool(row.get("realized_success"))]
    alerted_successes = [row for row in alerted if bool(row.get("realized_success"))]
    false_alerts = [row for row in alerted if not bool(row.get("realized_success"))]
    missed_successes = [row for row in non_alerted if bool(row.get("realized_success"))]
    outcomes = [float(row["realized_outcome_score"]) for row in alerted if row.get("realized_outcome_score") is not None]
    return {
        "theme_count": len(rows),
        "alert_count": len(alerted),
        "success_count": len(successes),
        "alerted_success_count": len(alerted_successes),
        "false_alert_count": len(false_alerts),
        "missed_success_count": len(missed_successes),
        "precision": _safe_div(len(alerted_successes), len(alerted)),
        "recall": _safe_div(len(alerted_successes), len(successes)),
        "false_alert_share": _safe_div(len(false_alerts), len(alerted)),
        "average_alerted_outcome_score": round(sum(outcomes) / len(outcomes), 4) if outcomes else None,
    }


def _evaluate_matured(root: Path, registry: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    by_horizon: dict[str, list[dict[str, Any]]] = {str(h): [] for h in policy["evaluation_horizons_months"]}
    for item in registry.get("snapshots") or []:
        snapshot_id = str(item["snapshot_id"])
        decision_path = root / "prospective_history/v61_policy_snapshots" / f"{snapshot_id}.json"
        if not decision_path.is_file():
            continue
        decisions = {row["theme_id"]: row for row in load_json(decision_path).get("decisions") or []}
        v50_item = next((row for row in (load_json(root / "prospective_history/v50_snapshot_registry.json").get("snapshots") or []) if row.get("snapshot_id") == snapshot_id), None)
        if not v50_item:
            continue
        for horizon, relative in (v50_item.get("evaluations") or {}).items():
            evaluation_path = root / str(relative)
            if not evaluation_path.is_file():
                continue
            evaluation = load_json(evaluation_path)
            joined: list[dict[str, Any]] = []
            for outcome in evaluation.get("themes") or []:
                decision = decisions.get(outcome.get("theme_id"))
                if not decision:
                    continue
                joined.append({**outcome, "champion_live_alert": decision["champion_live_alert"], "challenger_live_alert": decision["challenger_live_alert"]})
            by_horizon.setdefault(str(horizon), []).append({
                "snapshot_id": snapshot_id,
                "champion": _policy_metrics(joined, "champion_live_alert"),
                "challenger": _policy_metrics(joined, "challenger_live_alert"),
            })
    horizon_summary: dict[str, Any] = {}
    for horizon, evaluations in sorted(by_horizon.items(), key=lambda item: int(item[0])):
        horizon_summary[horizon] = {
            "matured_snapshot_count": len(evaluations),
            "evaluations": evaluations,
        }
    gate = policy["promotion_gate"]
    matured_checks = []
    for horizon in policy["evaluation_horizons_months"]:
        actual = horizon_summary[str(horizon)]["matured_snapshot_count"]
        required = int(gate["minimum_matured_snapshots"][str(horizon)])
        matured_checks.append({"horizon_months": horizon, "actual": actual, "required": required, "passed": actual >= required})
    return {
        "status": "PROSPECTIVE_POLICY_COMPARISON_ACCUMULATING",
        "horizons": horizon_summary,
        "minimum_maturity_checks": matured_checks,
        "promotion_evaluable": all(row["passed"] for row in matured_checks),
        "automatic_promotion_allowed": False,
    }


def run_v61(root: Path, output_dir: Path, run_date: str | None = None, v50_output_dir: Path | None = None, v60_output_dir: Path | None = None) -> dict[str, Any]:
    policy = load_json(root / "config/v61_live_shadow_policy.json")
    policy_lock = verify_policy_lock(root)
    v50_dir = v50_output_dir or root / "outputs/v50_final_validator"
    v60_dir = v60_output_dir or root / "outputs/v60_champion_challenger"
    required = [v50_dir / "v50_current_monthly_snapshot.json", v50_dir / "v50_run_summary.json", v60_dir / "v60_run_summary.json"]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise V61Error(f"required upstream outputs missing: {missing}")
    source_snapshot = load_json(v50_dir / "v50_current_monthly_snapshot.json")
    v50_summary = load_json(v50_dir / "v50_run_summary.json")
    v60_summary = load_json(v60_dir / "v60_run_summary.json")
    as_of = date.fromisoformat(run_date) if run_date else date.fromisoformat(str(source_snapshot["as_of"]))
    decisions = _decision_rows(source_snapshot, policy)
    champion_count = sum(bool(row["champion_live_alert"]) for row in decisions)
    challenger_count = sum(bool(row["challenger_live_alert"]) for row in decisions)
    snapshot_core = {
        "schema_version": 1,
        "engine_release": policy["engine_release"],
        "snapshot_id": source_snapshot["snapshot_id"],
        "as_of": source_snapshot["as_of"],
        "source_snapshot_sha256": source_snapshot["content_sha256"],
        "policy_id": policy["policy_id"],
        "policy_lock_sha256": policy_lock["actual_sha256"],
        "scope_warning": policy["scope_warning"],
        "champion_live_alert_count": champion_count,
        "challenger_live_alert_count": challenger_count,
        "challenger_added_alert_count": challenger_count - champion_count,
        "decisions": decisions,
        "investment_use_allowed": False,
    }
    snapshot = {**snapshot_core, "content_sha256": canonical_sha256(snapshot_core)}
    registry_path = root / "prospective_history/v61_policy_registry.json"
    registry = _load_registry(registry_path)
    action, stored_snapshot = _register_snapshot(root, snapshot, registry)
    registry["content_sha256"] = canonical_sha256({"schema_version": registry["schema_version"], "snapshots": registry["snapshots"]})
    write_json(registry_path, registry)
    scorecard = _evaluate_matured(root, registry, policy)
    next_due = v50_summary.get("next_evaluation_due")
    summary = {
        "status": "V6_1_PROSPECTIVE_POLICY_LEDGER_ACTIVE",
        "engine_release": policy["engine_release"],
        "as_of": as_of.isoformat(),
        "snapshot_action": action,
        "snapshot_count": len(registry["snapshots"]),
        "theme_count": len(decisions),
        "champion_live_alert_count": champion_count,
        "challenger_live_alert_count": challenger_count,
        "challenger_added_alert_count": challenger_count - champion_count,
        "added_theme_ids": [row["theme_id"] for row in decisions if row["added_by_challenger"]],
        "policy_lock": policy_lock,
        "historical_reference": {
            "champion_benchmark_recall": v60_summary["benchmark"]["champion"]["positive_recall"],
            "challenger_benchmark_recall": v60_summary["benchmark"]["challenger"]["positive_recall"],
            "champion_blind_recall": v60_summary["blind_holdout"]["champion"]["positive_recall"],
            "challenger_blind_recall": v60_summary["blind_holdout"]["challenger"]["positive_recall"],
            "warning": "역사 성적은 정책 설계에 사용된 자료이므로 미래 승격 근거로 단독 사용하지 않습니다."
        },
        "prospective_scorecard": scorecard,
        "next_evaluation_due": next_due,
        "challenger_promotion_allowed": False,
        "investment_use_allowed": False,
        "manual_run_required_after_bootstrap": False,
        "next_required_gate": "ACCUMULATE_LOCKED_6_12_24_MONTH_CHAMPION_CHALLENGER_OUTCOMES",
    }
    dashboard = {
        "status": summary["status"],
        "as_of": summary["as_of"],
        "progress": {
            "software_build_percent": 100,
            "data_pipeline_percent": 90,
            "historical_validation_percent": 70,
            "prospective_validation_percent": 0 if not scorecard["promotion_evaluable"] else 100,
            "overall_estimate_percent": 78 if not scorecard["promotion_evaluable"] else 95,
        },
        "champion_live_alert_count": champion_count,
        "challenger_live_alert_count": challenger_count,
        "added_theme_ids": summary["added_theme_ids"],
        "next_evaluation_due": next_due,
        "top_20": stored_snapshot["decisions"][:20],
        "prospective_scorecard": scorecard,
        "investment_use_allowed": False,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "v61_run_summary.json", summary)
    write_json(output_dir / "v61_current_policy_snapshot.json", stored_snapshot)
    write_json(output_dir / "v61_policy_registry.json", registry)
    write_json(output_dir / "v61_prospective_scorecard.json", scorecard)
    write_json(output_dir / "v61_policy_lock_verification.json", policy_lock)
    write_json(output_dir / "v61_dashboard_payload.json", dashboard)
    write_json(output_dir / "v61_next_gate.json", {
        "current_status": summary["status"],
        "next_required_gate": summary["next_required_gate"],
        "next_evaluation_due": next_due,
        "manual_run_required_after_bootstrap": False,
        "investment_use_allowed": False,
    })
    return summary
