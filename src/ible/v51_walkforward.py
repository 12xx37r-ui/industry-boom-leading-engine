from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ible.integrity import canonical_sha256, file_sha256, load_json, write_json
from ible.model_lock import load_and_verify_model_lock


class V51Error(RuntimeError):
    pass


def _safe_div(numerator: int | float, denominator: int) -> float | None:
    return round(float(numerator) / denominator, 4) if denominator else None


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [row for row in rows if str(row.get("status")) != "INSUFFICIENT_DATA"]
    positives = [row for row in eligible if str(row.get("label")).lower() == "positive"]
    negatives = [row for row in eligible if str(row.get("label")).lower() == "negative"]
    true_positive_alerts = sum(bool(row.get("alert_triggered")) and bool(row.get("passed")) for row in positives)
    false_positive_alerts = sum(bool(row.get("alert_triggered")) for row in negatives)
    positive_recall = _safe_div(sum(bool(row.get("passed")) for row in positives), len(positives))
    false_alarm_rate = _safe_div(false_positive_alerts, len(negatives))
    specificity = _safe_div(sum(bool(row.get("passed")) for row in negatives), len(negatives))
    balanced = None
    if positive_recall is not None and specificity is not None:
        balanced = round((positive_recall + specificity) / 2.0, 4)
    alert_precision = _safe_div(true_positive_alerts, true_positive_alerts + false_positive_alerts)
    return {
        "scenario_count": len(rows),
        "eligible_scenario_count": len(eligible),
        "positive_count": len(positives),
        "negative_count": len(negatives),
        "positive_pass_count": sum(bool(row.get("passed")) for row in positives),
        "negative_pass_count": sum(bool(row.get("passed")) for row in negatives),
        "positive_recall": positive_recall,
        "false_alarm_rate": false_alarm_rate,
        "specificity": specificity,
        "balanced_score": balanced,
        "alert_precision": alert_precision,
    }


def _normalise_benchmark(payload: dict[str, Any], relative_path: str, sha256: str) -> dict[str, Any]:
    observed = payload.get("observed") or {}
    return {
        "case_id": payload.get("scenario_id"),
        "case_name": payload.get("scenario_name"),
        "role": "LOCKED_HISTORICAL_RESEARCH_BENCHMARK",
        "label": payload.get("label"),
        "as_of": payload.get("as_of"),
        "target_theme_id": payload.get("target_theme_id"),
        "status": payload.get("status"),
        "passed": bool(payload.get("passed")),
        "alert_triggered": bool(payload.get("alert_triggered")),
        "rank": observed.get("rank"),
        "boom_score": observed.get("boom_score"),
        "early_signal_score": observed.get("early_signal_score"),
        "stage": observed.get("stage"),
        "data_confidence": observed.get("data_confidence"),
        "seed_file": relative_path,
        "seed_sha256": sha256,
    }


def _audit_point_in_time(payload: dict[str, Any]) -> dict[str, Any]:
    case_id = str(payload.get("scenario_id") or "")
    as_of_text = str(payload.get("as_of") or "")
    try:
        as_of = date.fromisoformat(as_of_text)
    except ValueError:
        return {"case_id": case_id, "status": "FAILED", "failures": ["invalid_as_of"]}
    failures: list[str] = []
    target = payload.get("target_result") or {}
    if str(target.get("as_of") or "") != as_of_text:
        failures.append("target_as_of_mismatch")
    ranking = payload.get("ranking") or []
    if any(str(row.get("as_of") or "") != as_of_text for row in ranking):
        failures.append("ranking_as_of_mismatch")
    future_capex_years: list[int] = []
    quality = ((payload.get("metadata") or {}).get("annual_capex_quality") or {})
    for company in quality.values():
        for raw_year in ((company or {}).get("details") or {}).keys():
            try:
                year = int(raw_year)
            except (TypeError, ValueError):
                continue
            if year >= as_of.year:
                future_capex_years.append(year)
    if future_capex_years:
        failures.append("future_or_same_year_annual_capex_detected")
    return {
        "case_id": case_id,
        "as_of": as_of_text,
        "status": "STRUCTURAL_CHECK_PASSED" if not failures else "FAILED",
        "failures": failures,
        "maximum_annual_capex_year": max(
            [int(year) for company in quality.values() for year in ((company or {}).get("details") or {}).keys() if str(year).isdigit()],
            default=None,
        ),
        "stored_result_vintage_proof": "PARTIAL_ONLY",
    }


def _gate(metrics: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    checks = [
        {
            "check": "positive_scenario_count",
            "actual": metrics["positive_count"],
            "required": int(config["minimum_benchmark_positive_scenarios"]),
            "passed": metrics["positive_count"] >= int(config["minimum_benchmark_positive_scenarios"]),
        },
        {
            "check": "negative_scenario_count",
            "actual": metrics["negative_count"],
            "required": int(config["minimum_benchmark_negative_scenarios"]),
            "passed": metrics["negative_count"] >= int(config["minimum_benchmark_negative_scenarios"]),
        },
        {
            "check": "positive_recall",
            "actual": metrics["positive_recall"],
            "required": float(config["minimum_positive_recall"]),
            "passed": metrics["positive_recall"] is not None and metrics["positive_recall"] >= float(config["minimum_positive_recall"]),
        },
        {
            "check": "false_alarm_rate",
            "actual": metrics["false_alarm_rate"],
            "required_maximum": float(config["maximum_false_alarm_rate"]),
            "passed": metrics["false_alarm_rate"] is not None and metrics["false_alarm_rate"] <= float(config["maximum_false_alarm_rate"]),
        },
        {
            "check": "balanced_score",
            "actual": metrics["balanced_score"],
            "required": float(config["minimum_balanced_score"]),
            "passed": metrics["balanced_score"] is not None and metrics["balanced_score"] >= float(config["minimum_balanced_score"]),
        },
    ]
    return {"passed": all(item["passed"] for item in checks), "checks": checks}


def run_v51(root: Path, output_dir: Path, run_date: str | None = None, v50_output_dir: Path | None = None) -> dict[str, Any]:
    config = load_json(root / "config/v51_historical_validation.json")
    seed_manifest = load_json(root / str(config["seed_manifest"]))
    model_lock = load_and_verify_model_lock(root)
    timezone = ZoneInfo("Asia/Seoul")
    now = datetime.now(timezone)
    as_of = date.fromisoformat(run_date) if run_date else now.date()

    seed_checks: list[dict[str, Any]] = []
    for relative, expected in sorted((seed_manifest.get("files") or {}).items()):
        path = root / relative
        actual = file_sha256(path) if path.is_file() else None
        seed_checks.append({"file": relative, "expected_sha256": expected, "actual_sha256": actual, "status": "MATCH" if actual == expected else "FAILED"})
    if any(item["status"] != "MATCH" for item in seed_checks):
        raise V51Error("sealed historical seed verification failed")

    benchmark_rows: list[dict[str, Any]] = []
    point_in_time_checks: list[dict[str, Any]] = []
    benchmark_dir = root / str(config["benchmark_seed_dir"])
    for path in sorted(benchmark_dir.glob("*.json")):
        payload = load_json(path)
        relative = str(path.relative_to(root)).replace("\\", "/")
        benchmark_rows.append(_normalise_benchmark(payload, relative, file_sha256(path)))
        point_in_time_checks.append(_audit_point_in_time(payload))

    blind_rows = load_json(root / str(config["blind_ranking_file"]))
    blind_rows = [dict(row, role="SEALED_THEME_DATE_HOLDOUT_NOT_EXTERNAL") for row in blind_rows]
    benchmark_metrics = _aggregate(benchmark_rows)
    blind_metrics = _aggregate(blind_rows)
    benchmark_gate = _gate(benchmark_metrics, config)
    blind_gate = _gate(blind_metrics, config)
    structural_point_in_time_passed = all(item["status"] == "STRUCTURAL_CHECK_PASSED" for item in point_in_time_checks)
    external_independence = bool(config.get("external_independence"))
    historical_research_gate_passed = benchmark_gate["passed"] and blind_gate["passed"] and structural_point_in_time_passed
    investment_allowed = historical_research_gate_passed and external_independence

    v50_dir = v50_output_dir or (root / "outputs/v50_final_validator")
    v50_summary = load_json(v50_dir / "v50_run_summary.json") if (v50_dir / "v50_run_summary.json").is_file() else None
    v50_dashboard = load_json(v50_dir / "v50_dashboard_payload.json") if (v50_dir / "v50_dashboard_payload.json").is_file() else None

    audit_core = {
        "schema_version": 1,
        "engine_release": config["engine_release"],
        "model_lock": model_lock,
        "seed_manifest_sha256": canonical_sha256(seed_manifest),
        "seed_checks": seed_checks,
        "benchmark": {"metrics": benchmark_metrics, "gate": benchmark_gate, "scenarios": benchmark_rows},
        "sealed_blind_holdout": {"metrics": blind_metrics, "gate": blind_gate, "cases": blind_rows, "external_independence": False},
        "point_in_time_audit": {
            "status": "PARTIAL_STRUCTURAL_PASS" if structural_point_in_time_passed else "FAILED",
            "structural_checks_passed": structural_point_in_time_passed,
            "full_source_vintage_independently_proven": False,
            "checks": point_in_time_checks,
        },
        "historical_research_gate_passed": historical_research_gate_passed,
        "external_independence": external_independence,
        "investment_use_allowed": investment_allowed,
        "known_limitations": config["known_limitations"],
    }
    audit_core["content_sha256"] = canonical_sha256(audit_core)
    receipt_path = root / "historical_history/v51_audits/v5.1.0-sealed-audit.json"
    if receipt_path.is_file():
        stored = load_json(receipt_path)
        if stored.get("content_sha256") != audit_core["content_sha256"]:
            raise V51Error("immutable historical audit receipt mismatch")
        audit_receipt = stored
        receipt_action = "REUSED_IMMUTABLE_AUDIT_RECEIPT"
    else:
        write_json(receipt_path, audit_core)
        audit_receipt = audit_core
        receipt_action = "CREATED_IMMUTABLE_AUDIT_RECEIPT"

    status = "V5_1_HISTORICAL_AUDIT_PASSED_NOT_EXTERNAL" if historical_research_gate_passed else "V5_1_HISTORICAL_AUDIT_COMPLETED_RECALL_GAP"
    summary = {
        "status": status,
        "engine_release": config["engine_release"],
        "as_of": as_of.isoformat(),
        "audit_receipt_action": receipt_action,
        "benchmark_metrics": benchmark_metrics,
        "blind_metrics": blind_metrics,
        "benchmark_gate_passed": benchmark_gate["passed"],
        "blind_gate_passed": blind_gate["passed"],
        "historical_research_gate_passed": historical_research_gate_passed,
        "external_independence": external_independence,
        "model_lock": model_lock,
        "prospective_validator": v50_summary,
        "investment_use_allowed": investment_allowed,
        "next_required_gate": "CONTINUE_MONTHLY_POINT_IN_TIME_ARCHIVE_AND_6_12_24_MONTH_PROSPECTIVE_SCORING",
        "manual_run_required_after_bootstrap": False,
    }
    dashboard = {
        "status": status,
        "as_of": as_of.isoformat(),
        "investment_use_allowed": investment_allowed,
        "historical_validation": {
            "benchmark_metrics": benchmark_metrics,
            "blind_metrics": blind_metrics,
            "benchmark_gate_passed": benchmark_gate["passed"],
            "blind_gate_passed": blind_gate["passed"],
            "external_independence": external_independence,
            "warning": "과거 7개 벤치마크의 성공산업 재현율이 기준 미달이면 최종 검증 통과로 표시하지 않습니다.",
        },
        "prospective": v50_dashboard,
    }
    next_gate = {
        "current_status": status,
        "failed_checks": [item for item in benchmark_gate["checks"] if not item["passed"]] + [item for item in blind_gate["checks"] if not item["passed"]],
        "next_required_gate": summary["next_required_gate"],
        "manual_run_required_after_bootstrap": False,
        "investment_use_allowed": investment_allowed,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "v51_run_summary.json", summary)
    write_json(output_dir / "v51_historical_audit.json", audit_receipt)
    write_json(output_dir / "v51_benchmark_scenarios.json", benchmark_rows)
    write_json(output_dir / "v51_blind_holdout_cases.json", blind_rows)
    write_json(output_dir / "v51_dashboard_payload.json", dashboard)
    write_json(output_dir / "v51_model_lock_verification.json", model_lock)
    write_json(output_dir / "v51_next_gate.json", next_gate)
    return summary
