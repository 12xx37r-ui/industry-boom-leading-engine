from __future__ import annotations

import datetime as dt
import json
import statistics
from pathlib import Path
from typing import Any

from ible.backtest import evaluate_scenario
from ible.collectors.sec_fsds import METRICS, SecFsdsClient
from ible.config import load_yaml
from ible.global_validation import _theme_result
from ible.model_lock import ModelLockError, load_and_verify_model_lock
from ible.offline_seed_builder import compute_seed_sha256


def _months_between(start: str, end: str) -> float:
    start_date = dt.date.fromisoformat(start)
    end_date = dt.date.fromisoformat(end)
    return round((end_date - start_date).days / 30.4375, 1)


def _load_seed(
    root: Path,
    seed_path: Path,
    tickers: list[str],
) -> tuple[dict[str, dict[str, list[tuple[str, float]]]], dict[str, Any], dict[str, str], dict[str, Any]]:
    try:
        seed = json.loads(seed_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"V1 holdout seed unavailable or invalid: {exc}") from exc
    expected_hash = str((seed.get("metadata") or {}).get("content_sha256") or "")
    actual_hash = compute_seed_sha256(seed)
    if not expected_hash or expected_hash != actual_hash:
        raise RuntimeError("V1 holdout seed integrity SHA-256 mismatch")
    client = SecFsdsClient(root / ".cache" / "sec_fsds_v1", "offline-seed@example.invalid")
    series, status, errors = client.load_seed(seed_path, tickers)
    return series, status, errors, seed


def _pairwise_auc(positives: list[dict[str, Any]], negatives: list[dict[str, Any]]) -> float | None:
    comparisons: list[float] = []
    for positive in positives:
        for negative in negatives:
            p = float((positive.get("observed") or {}).get("boom_score") or 0.0)
            n = float((negative.get("observed") or {}).get("boom_score") or 0.0)
            comparisons.append(1.0 if p > n else 0.5 if p == n else 0.0)
    return statistics.mean(comparisons) if comparisons else None


def run_v1_walkforward(root: Path, output_dir: Path) -> dict[str, Any]:
    config = load_yaml(root / "config" / "v1_walkforward.yml")
    exposure_config = load_yaml(root / str(config["exposure_file"]))
    lock_result = load_and_verify_model_lock(root)

    minimum_exposure = float(exposure_config.get("minimum_exposure", 0.30))
    themes_by_id = {str(row["id"]): row for row in exposure_config.get("themes", [])}
    cohort_ids = [str(value) for value in config["cohort"]["theme_ids"]]
    missing_themes = sorted(set(cohort_ids) - set(themes_by_id))
    if missing_themes:
        raise RuntimeError(f"V1 exposure taxonomy missing themes: {missing_themes}")
    themes = [themes_by_id[theme_id] for theme_id in cohort_ids]
    as_of = str(config["cohort"]["as_of"])
    seed_path = root / str(config["seed_file"])
    tickers = sorted(
        {
            str(company["ticker"]).upper()
            for theme in themes
            for company in theme.get("us_companies", [])
            if float(company.get("exposure", 0)) >= minimum_exposure
        }
    )

    all_series, financial_status, financial_errors, seed = _load_seed(root, seed_path, tickers)
    metadata = seed.get("metadata") or {}
    if str(metadata.get("cutoff") or "") != as_of:
        raise RuntimeError(f"V1 seed cutoff mismatch: expected {as_of}, got {metadata.get('cutoff')}")

    historically_eligible = set(financial_status.get("historically_eligible") or [])
    research = {
        str(key): value
        for key, value in (seed.get("research") or {}).items()
        if isinstance(value, dict)
    }
    gates = dict(config.get("gates") or {})
    required_research = set(cohort_ids)
    research_available = len(required_research & set(research))
    dataset_gate_passed = bool(
        financial_status.get("status") == "READY"
        and float(financial_status.get("coverage_of_historically_eligible") or 0.0)
        >= float(gates.get("financial_coverage_min", 0.72))
        and int(financial_status.get("available") or 0)
        >= int(gates.get("available_company_min", 24))
        and research_available >= int(gates.get("research_theme_min", 6))
    )

    ranking = [
        _theme_result(
            theme,
            all_series,
            research.get(theme["id"]),
            as_of,
            minimum_exposure,
            historically_eligible,
        )
        for theme in themes
    ]
    ranking.sort(key=lambda row: (row["boom_score"], row["data_confidence"]), reverse=True)

    scenarios: list[dict[str, Any]] = []
    for raw in config.get("scenarios", []):
        scenario = dict(raw)
        scenario["as_of"] = as_of
        scenario["comparison_theme_ids"] = cohort_ids
        evaluated = evaluate_scenario(scenario, ranking)
        evaluated["outcome_class"] = scenario.get("outcome_class")
        boom_start = scenario.get("boom_start")
        evaluated["lead_time_months"] = _months_between(as_of, str(boom_start)) if boom_start else None
        if not dataset_gate_passed:
            evaluated["status"] = "INSUFFICIENT_DATA"
            evaluated["passed"] = False
            evaluated["reason"] = "V1 독립검증 데이터 게이트 미통과"
        scenarios.append(evaluated)

    eligible = [row for row in scenarios if row.get("status") != "INSUFFICIENT_DATA"]
    positives = [row for row in eligible if row.get("label") == "positive"]
    negatives = [row for row in eligible if row.get("label") == "negative"]
    recall = sum(bool(row.get("passed")) for row in positives) / len(positives) if positives else None
    false_alarm = (
        sum(bool(row.get("alert_triggered")) for row in negatives) / len(negatives)
        if negatives
        else None
    )
    auc = _pairwise_auc(positives, negatives)
    ai_scenario = next((row for row in eligible if row.get("target_theme_id") == "AI_COMPUTE_INFRA"), None)
    ai_passed = bool(ai_scenario and ai_scenario.get("passed"))
    eligible_leads = [float(row["lead_time_months"]) for row in positives if row.get("passed") and row.get("lead_time_months") is not None]
    median_lead = statistics.median(eligible_leads) if eligible_leads else None

    passed = bool(
        dataset_gate_passed
        and recall is not None
        and recall >= float(gates.get("positive_recall_min", 0.75))
        and false_alarm is not None
        and false_alarm <= float(gates.get("false_alarm_rate_max", 0.3334))
        and auc is not None
        and auc >= float(gates.get("pairwise_auc_min", 0.70))
        and (ai_passed or not bool(gates.get("ai_target_must_pass", True)))
    )
    status = (
        "V1_AI_LOCKED_REPLAY_PASSED"
        if passed
        else "V1_AI_LOCKED_REPLAY_FAILED"
        if dataset_gate_passed
        else "V1_AI_LOCKED_REPLAY_INSUFFICIENT_DATA"
    )

    summary = {
        "status": status,
        "investment_use_allowed": False,
        "validation_role": config.get("validation_role"),
        "engine_release": config.get("engine_release"),
        "frozen_model_version": config.get("model_version"),
        "model_lock": lock_result,
        "seed_file": str(config["seed_file"]),
        "seed_content_sha256": metadata.get("content_sha256"),
        "as_of": as_of,
        "outcome_observation_end": config["cohort"].get("outcome_observation_end"),
        "dataset_gate_passed": dataset_gate_passed,
        "metrics": {
            "eligible_scenarios": len(eligible),
            "positive_recall": round(recall, 4) if recall is not None else None,
            "false_alarm_rate": round(false_alarm, 4) if false_alarm is not None else None,
            "pairwise_auc": round(auc, 4) if auc is not None else None,
            "ai_target_passed": ai_passed,
            "median_successful_lead_months": round(median_lead, 1) if median_lead is not None else None,
        },
        "criteria": gates,
        "financial_data": financial_status,
        "financial_data_errors": financial_errors,
        "research_available": research_available,
        "research_required": len(required_research),
        "ranking": ranking,
        "scenarios": scenarios,
        "known_limitations": [
            "V1 AI 재현은 V0.9.1 계산식과 판정 로직을 해시로 동결한 뒤 실행합니다.",
            "SEC·arXiv 자료는 PC에서 한 번 수집한 점시점 seed만 사용하며 GitHub 실행 중 외부 API를 호출하지 않습니다.",
            "기업의 산업 노출도는 보수적 수동 분류이며 사업부 공시 기반 정밀화가 추가로 필요합니다.",
            "산업 결과 라벨은 2025-12-31까지의 광범위한 산업화 여부를 기준으로 한 연구용 벤치마크입니다.",
            "AI_2022 시나리오는 이전 백테스트에 이미 존재했으므로 이 결과는 독립 홀드아웃 합격 판정에 사용하지 않습니다.",
            "시장 미반영도·주가수익·거래비용 검증 전에는 투자 사용을 허용하지 않습니다.",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "v1_walkforward_summary.json": summary,
        "v1_walkforward_ranking.json": ranking,
        "v1_walkforward_scenarios.json": scenarios,
        "v1_model_lock_verification.json": lock_result,
        "v1_financial_data_status.json": financial_status,
    }
    for name, payload in outputs.items():
        (output_dir / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
