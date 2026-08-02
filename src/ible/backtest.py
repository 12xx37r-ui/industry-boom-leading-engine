from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from ible.config import load_yaml


def load_scenarios(root: Path) -> dict[str, dict[str, Any]]:
    config = load_yaml(root / "config" / "backtests.yml")
    scenarios = config.get("scenarios") or []
    return {str(item["id"]): item for item in scenarios}


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def evaluate_scenario(scenario: dict[str, Any], ranking: list[dict[str, Any]]) -> dict[str, Any]:
    target_id = str(scenario["target_theme_id"])
    target = next((row for row in ranking if row.get("theme_id") == target_id), None)
    rank = next((idx + 1 for idx, row in enumerate(ranking) if row.get("theme_id") == target_id), None)
    label = str(scenario.get("label") or "positive").lower()
    criteria = dict(scenario.get("criteria") or {})

    if target is None:
        return {
            "scenario_id": scenario["id"],
            "scenario_name": scenario.get("name"),
            "label": label,
            "as_of": scenario.get("as_of"),
            "target_theme_id": target_id,
            "status": "INSUFFICIENT_DATA",
            "passed": False,
            "alert_triggered": False,
            "reason": "목표 산업이 비교군 결과에 없습니다.",
            "ranking": ranking,
        }

    score = _finite(target.get("boom_score"))
    early = _finite(target.get("early_signal_score"))
    confidence = _finite(target.get("data_confidence"))
    stage = str(target.get("stage") or "")
    allowed_stages = {"EARLY_ACCUMULATION", "TRANSITION", "COMMERCIAL_BOOM"}

    if label == "positive":
        rank_max = int(criteria.get("rank_max", 4))
        score_min = _finite(criteria.get("score_min"), 55.0)
        early_min = _finite(criteria.get("early_signal_min"), 55.0)
        alert_triggered = bool(
            rank is not None
            and rank <= rank_max
            and score >= score_min
            and early >= early_min
            and stage in allowed_stages
        )
        passed = alert_triggered
        reason = (
            "선행경보 기준을 통과했습니다."
            if passed
            else "순위·선행점수·초기축적 단계 중 하나 이상이 기준에 미달했습니다."
        )
    else:
        strong_rank_max = int(criteria.get("strong_alert_rank_max", 3))
        strong_score_min = _finite(criteria.get("strong_alert_score_min"), 60.0)
        strong_early_min = _finite(criteria.get("strong_alert_early_min"), 58.0)
        alert_triggered = bool(
            rank is not None
            and rank <= strong_rank_max
            and score >= strong_score_min
            and early >= strong_early_min
            and stage in allowed_stages
        )
        passed = not alert_triggered
        reason = (
            "강한 선행경보를 내지 않아 음성 통제군을 통과했습니다."
            if passed
            else "실패·과열 통제군에 강한 선행경보가 발생했습니다."
        )

    data_eligible = confidence >= 35.0 and int(target.get("coverage", {}).get("usable_companies", 0)) >= 1
    if not data_eligible:
        status = "INSUFFICIENT_DATA"
        passed = False
        reason = "기업·재무 데이터 확보가 부족해 합격·실패 판정에서 제외해야 합니다."
    else:
        status = "PASSED" if passed else "FAILED"

    return {
        "scenario_id": scenario["id"],
        "scenario_name": scenario.get("name"),
        "label": label,
        "as_of": scenario.get("as_of"),
        "target_theme_id": target_id,
        "comparison_theme_ids": scenario.get("comparison_theme_ids", []),
        "status": status,
        "passed": passed,
        "data_eligible": data_eligible,
        "alert_triggered": alert_triggered,
        "reason": reason,
        "observed": {
            "rank": rank,
            "cohort_size": len(ranking),
            "boom_score": score,
            "early_signal_score": early,
            "commercial_realization_score": target.get("commercial_realization_score"),
            "cross_confirmation_score": target.get("cross_confirmation_score"),
            "stage": stage,
            "data_confidence": confidence,
            "company_coverage": target.get("coverage", {}).get("company_coverage"),
            "usable_companies": target.get("coverage", {}).get("usable_companies"),
        },
        "criteria": criteria,
        "target_result": target,
        "ranking": ranking,
    }


def aggregate_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [row for row in results if row.get("status") != "INSUFFICIENT_DATA"]
    positives = [row for row in eligible if row.get("label") == "positive"]
    negatives = [row for row in eligible if row.get("label") == "negative"]
    positive_passes = sum(bool(row.get("passed")) for row in positives)
    negative_passes = sum(bool(row.get("passed")) for row in negatives)
    positive_recall = positive_passes / len(positives) if positives else None
    false_alarm_rate = (
        sum(bool(row.get("alert_triggered")) for row in negatives) / len(negatives)
        if negatives else None
    )
    specificity = negative_passes / len(negatives) if negatives else None
    balanced_score = None
    if positive_recall is not None and specificity is not None:
        balanced_score = (positive_recall + specificity) / 2.0

    stage2_passed = bool(
        len(positives) >= 3
        and len(negatives) >= 2
        and positive_recall is not None
        and positive_recall >= 2 / 3
        and false_alarm_rate is not None
        and false_alarm_rate <= 1 / 3
        and balanced_score is not None
        and balanced_score >= 0.67
    )
    return {
        "status": "PASSED_STAGE2_RESEARCH" if stage2_passed else "FAILED_STAGE2",
        "investment_use_allowed": False,
        "stage2_passed": stage2_passed,
        "metrics": {
            "scenario_count": len(results),
            "eligible_scenario_count": len(eligible),
            "positive_count": len(positives),
            "negative_count": len(negatives),
            "positive_recall": round(positive_recall, 4) if positive_recall is not None else None,
            "false_alarm_rate": round(false_alarm_rate, 4) if false_alarm_rate is not None else None,
            "specificity": round(specificity, 4) if specificity is not None else None,
            "balanced_score": round(balanced_score, 4) if balanced_score is not None else None,
        },
        "criteria": {
            "minimum_positive_scenarios": 3,
            "minimum_negative_scenarios": 2,
            "positive_recall_min": round(2 / 3, 4),
            "false_alarm_rate_max": round(1 / 3, 4),
            "balanced_score_min": 0.67,
        },
        "reason": (
            "다중 성공·음성 통제군 연구검증을 통과했습니다. 다만 미국 원천수요, 시장 미반영도, 거래비용 검증 전에는 투자에 사용할 수 없습니다."
            if stage2_passed
            else "성공사례 재현율 또는 음성 통제군 허위경보율 기준을 통과하지 못했습니다."
        ),
        "scenarios": results,
        "known_limitations": [
            "비교군 순위는 각 시나리오의 축소 코호트 안에서 계산됩니다.",
            "OpenDART와 arXiv의 과거 데이터는 완전한 당시 빈티지 보장이 제한됩니다.",
            "역사적 성공·실패 라벨은 연구용 벤치마크이며 투자수익의 인과적 정답표가 아닙니다.",
            "주가 선반영도·거래비용·포트폴리오 손실통제는 아직 검증하지 않았습니다.",
        ],
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
