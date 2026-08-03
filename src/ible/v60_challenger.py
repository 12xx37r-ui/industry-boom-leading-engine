from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from ible.integrity import canonical_sha256, file_sha256, load_json, write_json
from ible.model_lock import load_and_verify_model_lock


class V60Error(RuntimeError):
    pass


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_div(numerator: int | float, denominator: int) -> float | None:
    return round(float(numerator) / denominator, 4) if denominator else None


def verify_policy_lock(root: Path) -> dict[str, Any]:
    lock = load_json(root / "config/v60_policy_lock.json")
    relative = str(lock["policy_file"])
    actual = file_sha256(root / relative)
    expected = str(lock["expected_sha256"])
    if actual != expected:
        raise V60Error(f"challenger policy lock mismatch: expected={expected} actual={actual}")
    policy = load_json(root / relative)
    if str(policy.get("policy_id")) != str(lock.get("sealed_policy_id")):
        raise V60Error("challenger policy id mismatch")
    return {
        "status": "POLICY_LOCK_VERIFIED",
        "policy_id": policy["policy_id"],
        "policy_file": relative,
        "expected_sha256": expected,
        "actual_sha256": actual,
        "sealed_at": lock.get("sealed_at"),
    }


def challenger_alert(row: dict[str, Any], policy: dict[str, Any]) -> tuple[bool, str]:
    if bool(row.get("alert_triggered")):
        return True, "CHAMPION_ALERT_PRESERVED"
    bridge = ((policy.get("challenger") or {}).get("watch_bridge") or {})
    if not bool(bridge.get("enabled")):
        return False, "NO_ALERT"
    stage = str(row.get("stage") or "")
    rank = row.get("rank")
    boom = _number(row.get("boom_score"))
    early = _number(row.get("early_signal_score"))
    confidence = _number(row.get("data_confidence"))
    bridge_passed = bool(
        stage == str(bridge.get("required_stage"))
        and rank is not None
        and int(rank) <= int(bridge.get("rank_max", 4))
        and boom >= _number(bridge.get("boom_score_min"), 58.0)
        and early >= _number(bridge.get("early_signal_score_min"), 57.5)
        and confidence >= _number(bridge.get("data_confidence_min"), 60.0)
    )
    return (True, "WATCH_BRIDGE_STRONG_CONFIRMATION") if bridge_passed else (False, "NO_ALERT")


def _decision(row: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    eligible = str(row.get("status")) != "INSUFFICIENT_DATA"
    label = str(row.get("label") or "").lower()
    champion_alert = bool(row.get("alert_triggered")) if eligible else False
    challenger, challenger_reason = challenger_alert(row, policy) if eligible else (False, "INSUFFICIENT_DATA")
    champion_passed = eligible and (champion_alert if label == "positive" else not champion_alert)
    challenger_passed = eligible and (challenger if label == "positive" else not challenger)
    return {
        "case_id": row.get("case_id"),
        "case_name": row.get("case_name"),
        "source_role": row.get("role"),
        "label": label,
        "as_of": row.get("as_of"),
        "data_eligible": eligible,
        "observed": {
            "rank": row.get("rank"),
            "boom_score": row.get("boom_score"),
            "early_signal_score": row.get("early_signal_score"),
            "stage": row.get("stage"),
            "data_confidence": row.get("data_confidence"),
        },
        "champion": {
            "alert_triggered": champion_alert,
            "passed": champion_passed,
        },
        "challenger": {
            "alert_triggered": challenger,
            "passed": challenger_passed,
            "decision_reason": challenger_reason,
        },
        "changed_by_challenger": champion_alert != challenger,
    }


def _metrics(decisions: list[dict[str, Any]], key: str) -> dict[str, Any]:
    eligible = [row for row in decisions if bool(row.get("data_eligible"))]
    positives = [row for row in eligible if row.get("label") == "positive"]
    negatives = [row for row in eligible if row.get("label") == "negative"]
    positive_pass = sum(bool((row.get(key) or {}).get("passed")) for row in positives)
    negative_pass = sum(bool((row.get(key) or {}).get("passed")) for row in negatives)
    false_alerts = sum(bool((row.get(key) or {}).get("alert_triggered")) for row in negatives)
    true_alerts = sum(bool((row.get(key) or {}).get("alert_triggered")) for row in positives)
    recall = _safe_div(positive_pass, len(positives))
    far = _safe_div(false_alerts, len(negatives))
    specificity = _safe_div(negative_pass, len(negatives))
    balanced = round((recall + specificity) / 2.0, 4) if recall is not None and specificity is not None else None
    precision = _safe_div(true_alerts, true_alerts + false_alerts)
    return {
        "scenario_count": len(decisions),
        "eligible_scenario_count": len(eligible),
        "positive_count": len(positives),
        "negative_count": len(negatives),
        "positive_pass_count": positive_pass,
        "negative_pass_count": negative_pass,
        "positive_recall": recall,
        "false_alarm_rate": far,
        "specificity": specificity,
        "balanced_score": balanced,
        "alert_precision": precision,
    }


def _delta(champion: dict[str, Any], challenger: dict[str, Any]) -> dict[str, Any]:
    keys = ["positive_recall", "false_alarm_rate", "specificity", "balanced_score", "alert_precision"]
    out: dict[str, Any] = {}
    for key in keys:
        left, right = champion.get(key), challenger.get(key)
        out[key] = round(float(right) - float(left), 4) if left is not None and right is not None else None
    return out


def _research_gate(
    benchmark_champion: dict[str, Any],
    benchmark_challenger: dict[str, Any],
    blind_champion: dict[str, Any],
    blind_challenger: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    gate = policy["research_gates"]
    checks = [
        {
            "check": "benchmark_positive_recall",
            "actual": benchmark_challenger["positive_recall"],
            "required": gate["benchmark_positive_recall_min"],
            "passed": benchmark_challenger["positive_recall"] is not None and benchmark_challenger["positive_recall"] >= gate["benchmark_positive_recall_min"],
        },
        {
            "check": "benchmark_false_alarm_rate",
            "actual": benchmark_challenger["false_alarm_rate"],
            "required_maximum": gate["benchmark_false_alarm_rate_max"],
            "passed": benchmark_challenger["false_alarm_rate"] is not None and benchmark_challenger["false_alarm_rate"] <= gate["benchmark_false_alarm_rate_max"],
        },
        {
            "check": "benchmark_balanced_score",
            "actual": benchmark_challenger["balanced_score"],
            "required": gate["benchmark_balanced_score_min"],
            "passed": benchmark_challenger["balanced_score"] is not None and benchmark_challenger["balanced_score"] >= gate["benchmark_balanced_score_min"],
        },
        {
            "check": "blind_positive_recall",
            "actual": blind_challenger["positive_recall"],
            "required": gate["blind_positive_recall_min"],
            "passed": blind_challenger["positive_recall"] is not None and blind_challenger["positive_recall"] >= gate["blind_positive_recall_min"],
        },
        {
            "check": "blind_false_alarm_rate",
            "actual": blind_challenger["false_alarm_rate"],
            "required_maximum": gate["blind_false_alarm_rate_max"],
            "passed": blind_challenger["false_alarm_rate"] is not None and blind_challenger["false_alarm_rate"] <= gate["blind_false_alarm_rate_max"],
        },
        {
            "check": "positive_recall_improved",
            "benchmark_delta": _delta(benchmark_champion, benchmark_challenger)["positive_recall"],
            "blind_delta": _delta(blind_champion, blind_challenger)["positive_recall"],
            "passed": benchmark_challenger["positive_recall"] > benchmark_champion["positive_recall"],
        },
        {
            "check": "no_false_alarm_increase",
            "benchmark_champion": benchmark_champion["false_alarm_rate"],
            "benchmark_challenger": benchmark_challenger["false_alarm_rate"],
            "blind_champion": blind_champion["false_alarm_rate"],
            "blind_challenger": blind_challenger["false_alarm_rate"],
            "passed": benchmark_challenger["false_alarm_rate"] <= benchmark_champion["false_alarm_rate"] and blind_challenger["false_alarm_rate"] <= blind_champion["false_alarm_rate"],
        },
    ]
    return {"passed": all(bool(item["passed"]) for item in checks), "checks": checks}


def run_v60(root: Path, output_dir: Path, run_date: str | None = None, v51_output_dir: Path | None = None) -> dict[str, Any]:
    as_of = date.fromisoformat(run_date) if run_date else date.today()
    v51_dir = v51_output_dir or root / "outputs/v51_historical_audit"
    required = ["v51_benchmark_scenarios.json", "v51_blind_holdout_cases.json", "v51_run_summary.json"]
    missing = [name for name in required if not (v51_dir / name).is_file()]
    if missing:
        raise V60Error(f"V5.1 outputs missing: {missing}")

    policy = load_json(root / "config/v60_challenger_policy.json")
    policy_lock = verify_policy_lock(root)
    model_lock = load_and_verify_model_lock(root)
    v51_summary = load_json(v51_dir / "v51_run_summary.json")
    benchmark_rows = load_json(v51_dir / "v51_benchmark_scenarios.json")
    blind_rows = load_json(v51_dir / "v51_blind_holdout_cases.json")

    benchmark_decisions = [_decision(row, policy) for row in benchmark_rows]
    blind_decisions = [_decision(row, policy) for row in blind_rows]
    bc = _metrics(benchmark_decisions, "champion")
    bx = _metrics(benchmark_decisions, "challenger")
    hc = _metrics(blind_decisions, "champion")
    hx = _metrics(blind_decisions, "challenger")
    gate = _research_gate(bc, bx, hc, hx, policy)

    changed_cases = [row["case_id"] for row in benchmark_decisions + blind_decisions if row["changed_by_challenger"]]

    # The V6.0 receipt seals a comparison of immutable V5.1 evidence.
    # v51_run_summary.as_of is the workflow execution date, not a change in the
    # sealed evidence. Re-hashing that volatile date made every later run fail.
    # When a receipt already exists, preserve its evidence_as_of and verify all
    # substantive fields against the newly recomputed comparison.
    receipt_path = root / "historical_history/v60_audits/v6.0.0-champion-challenger.json"
    existing_receipt = load_json(receipt_path) if receipt_path.is_file() else None
    evidence_as_of = (
        str(existing_receipt.get("evidence_as_of") or "")
        if existing_receipt is not None
        else str(v51_summary.get("as_of") or "")
    )

    comparison_core = {
        "schema_version": 1,
        "engine_release": policy["engine_release"],
        "evidence_as_of": evidence_as_of,
        "policy": policy,
        "policy_lock": policy_lock,
        "model_lock": model_lock,
        "evidence_status": {
            "external_independence": False,
            "diagnostic_reuse": True,
            "warning": "Challenger는 V5.1 진단 후 같은 역사·봉인 자료를 참고해 정의됐으므로 연구 게이트 통과가 곧 외부검증 또는 실전 승격을 뜻하지 않습니다."
        },
        "benchmark": {
            "champion": bc,
            "challenger": bx,
            "delta": _delta(bc, bx),
        },
        "blind_holdout": {
            "champion": hc,
            "challenger": hx,
            "delta": _delta(hc, hx),
        },
        "research_gate": gate,
        "changed_cases": changed_cases,
        "deployment": {
            "champion_remains_active": True,
            "challenger_mode": "PROSPECTIVE_SHADOW_ONLY",
            "automatic_promotion_allowed": False,
            "promotion_status": "NOT_ELIGIBLE_SAME_EVIDENCE",
            "investment_use_allowed": False,
        },
    }
    comparison_hash = canonical_sha256(comparison_core)
    comparison = {**comparison_core, "comparison_sha256": comparison_hash}

    if existing_receipt is not None:
        if existing_receipt.get("comparison_sha256") != comparison_hash:
            raise V60Error(
                "immutable V6.0 comparison receipt mismatch: "
                "sealed evidence or locked policy/model content changed"
            )
        # Return the byte-for-byte sealed receipt rather than rewriting it.
        comparison = existing_receipt
        receipt_action = "REUSED_IMMUTABLE_COMPARISON_RECEIPT"
    else:
        write_json(receipt_path, comparison)
        receipt_action = "CREATED_IMMUTABLE_COMPARISON_RECEIPT"

    status = "V6_0_CHALLENGER_RESEARCH_GATE_PASSED_SHADOW_ONLY" if gate["passed"] else "V6_0_CHALLENGER_RESEARCH_GATE_FAILED"
    summary = {
        "status": status,
        "engine_release": policy["engine_release"],
        "as_of": as_of.isoformat(),
        "comparison_receipt_action": receipt_action,
        "policy_lock": policy_lock,
        "model_lock": model_lock,
        "benchmark": comparison["benchmark"],
        "blind_holdout": comparison["blind_holdout"],
        "research_gate_passed": gate["passed"],
        "champion_remains_active": True,
        "challenger_mode": "PROSPECTIVE_SHADOW_ONLY",
        "challenger_promotion_allowed": False,
        "investment_use_allowed": False,
        "v51_status": v51_summary.get("status"),
        "next_required_gate": policy["promotion_rules"]["required_next_gate"],
        "manual_run_required_after_bootstrap": False,
    }
    dashboard = {
        "status": status,
        "as_of": as_of.isoformat(),
        "champion": {"model_version": "0.9.1", "active": True, "benchmark": bc, "blind": hc},
        "challenger": {"policy_id": policy["policy_id"], "mode": "SHADOW_ONLY", "benchmark": bx, "blind": hx},
        "changed_cases": changed_cases,
        "warning": comparison["evidence_status"]["warning"],
        "investment_use_allowed": False,
    }
    next_gate = {
        "current_status": status,
        "research_gate_passed": gate["passed"],
        "promotion_status": "NOT_ELIGIBLE_SAME_EVIDENCE",
        "next_required_gate": summary["next_required_gate"],
        "investment_use_allowed": False,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "v60_run_summary.json", summary)
    write_json(output_dir / "v60_champion_challenger_comparison.json", comparison)
    write_json(output_dir / "v60_case_decisions.json", {"benchmark": benchmark_decisions, "blind_holdout": blind_decisions})
    write_json(output_dir / "v60_policy_lock_verification.json", policy_lock)
    write_json(output_dir / "v60_model_lock_verification.json", model_lock)
    write_json(output_dir / "v60_dashboard_payload.json", dashboard)
    write_json(output_dir / "v60_next_gate.json", next_gate)
    return summary
