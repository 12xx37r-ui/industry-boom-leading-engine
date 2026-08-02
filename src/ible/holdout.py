from __future__ import annotations

import hashlib
import math
import statistics
from pathlib import Path
from typing import Any

from ible.backtest import evaluate_scenario
from ible.config import load_yaml


ALLOWED_STAGE3_STAGES = {
    "EARLY_ACCUMULATION",
    "CAPITAL_LED_ACCUMULATION",
    "TRANSITION",
    "COMMERCIAL_BOOM",
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_holdout_config(root: Path) -> dict[str, Any]:
    payload = load_yaml(root / "config" / "holdouts.yml")
    if not payload.get("scenarios"):
        raise ValueError("config/holdouts.yml has no scenarios")
    cohort = payload.get("cohort") or {}
    if not cohort.get("as_of") or not cohort.get("theme_ids"):
        raise ValueError("holdout cohort must define as_of and theme_ids")
    return payload


def verify_model_lock(root: Path) -> dict[str, Any]:
    lock_path = root / "config" / "model_lock.json"
    if not lock_path.exists():
        raise ValueError("config/model_lock.json missing")
    import json

    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    scoring_path = root / "src" / "ible" / "analytics" / "scoring.py"
    backtest_path = root / "src" / "ible" / "backtest.py"
    current = {
        "scoring_sha256": file_sha256(scoring_path),
        "backtest_sha256": file_sha256(backtest_path),
    }
    expected = {
        "scoring_sha256": str(lock.get("scoring_sha256") or ""),
        "backtest_sha256": str(lock.get("backtest_sha256") or ""),
    }
    matches = current == expected
    return {
        "status": "LOCKED_MODEL_VERIFIED" if matches else "LOCK_MISMATCH",
        "matches": matches,
        "frozen_model_version": lock.get("frozen_model_version"),
        "expected": expected,
        "current": current,
        "rule": "홀드아웃 실행 중 scoring.py와 backtest.py를 수정하면 검증은 무효입니다.",
    }


def evaluate_holdout_scenarios(
    config: dict[str, Any], ranking: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    cohort = config["cohort"]
    theme_ids = list(cohort["theme_ids"])
    by_id = {str(row.get("theme_id")): row for row in ranking}
    cohort_ranking = [by_id[theme_id] for theme_id in theme_ids if theme_id in by_id]
    cohort_ranking.sort(
        key=lambda row: (float(row.get("boom_score") or 0.0), float(row.get("data_confidence") or 0.0)),
        reverse=True,
    )
    results: list[dict[str, Any]] = []
    for raw in config["scenarios"]:
        scenario = dict(raw)
        scenario["comparison_theme_ids"] = theme_ids
        result = evaluate_scenario(scenario, cohort_ranking)
        result["holdout"] = True
        results.append(result)
    return results


def _pairwise_auc(positives: list[dict[str, Any]], negatives: list[dict[str, Any]]) -> float | None:
    pairs = 0
    wins = 0.0
    for positive in positives:
        p_score = float((positive.get("observed") or {}).get("boom_score") or 0.0)
        for negative in negatives:
            n_score = float((negative.get("observed") or {}).get("boom_score") or 0.0)
            pairs += 1
            if p_score > n_score:
                wins += 1.0
            elif p_score == n_score:
                wins += 0.5
    return wins / pairs if pairs else None


def _median_rank(rows: list[dict[str, Any]]) -> float | None:
    ranks = [float((row.get("observed") or {}).get("rank")) for row in rows if (row.get("observed") or {}).get("rank")]
    return statistics.median(ranks) if ranks else None


def aggregate_holdout_results(
    results: list[dict[str, Any]], lock_verification: dict[str, Any]
) -> dict[str, Any]:
    eligible = [row for row in results if row.get("status") != "INSUFFICIENT_DATA"]
    positives = [row for row in eligible if row.get("label") == "positive"]
    negatives = [row for row in eligible if row.get("label") == "negative"]
    positive_passes = sum(bool(row.get("passed")) for row in positives)
    false_alarms = sum(bool(row.get("alert_triggered")) for row in negatives)
    negative_passes = sum(bool(row.get("passed")) for row in negatives)

    recall = positive_passes / len(positives) if positives else None
    false_alarm_rate = false_alarms / len(negatives) if negatives else None
    specificity = negative_passes / len(negatives) if negatives else None
    balanced = (
        (recall + specificity) / 2.0
        if recall is not None and specificity is not None
        else None
    )
    pairwise_auc = _pairwise_auc(positives, negatives)
    positive_median_rank = _median_rank(positives)
    negative_median_rank = _median_rank(negatives)

    criteria = {
        "minimum_positive_scenarios": 3,
        "minimum_negative_scenarios": 3,
        "positive_recall_min": 0.75,
        "false_alarm_rate_max": 0.25,
        "balanced_score_min": 0.75,
        "pairwise_auc_min": 0.70,
        "model_lock_required": True,
    }
    passed = bool(
        lock_verification.get("matches")
        and len(positives) >= criteria["minimum_positive_scenarios"]
        and len(negatives) >= criteria["minimum_negative_scenarios"]
        and recall is not None
        and recall >= criteria["positive_recall_min"]
        and false_alarm_rate is not None
        and false_alarm_rate <= criteria["false_alarm_rate_max"]
        and balanced is not None
        and balanced >= criteria["balanced_score_min"]
        and pairwise_auc is not None
        and pairwise_auc >= criteria["pairwise_auc_min"]
    )

    def rounded(value: float | None) -> float | None:
        return round(value, 4) if value is not None and math.isfinite(value) else None

    return {
        "status": "PASSED_STAGE3_HOLDOUT" if passed else "FAILED_STAGE3_HOLDOUT",
        "stage3_passed": passed,
        "investment_use_allowed": False,
        "model_lock": lock_verification,
        "metrics": {
            "scenario_count": len(results),
            "eligible_scenario_count": len(eligible),
            "positive_count": len(positives),
            "negative_count": len(negatives),
            "positive_recall": rounded(recall),
            "false_alarm_rate": rounded(false_alarm_rate),
            "specificity": rounded(specificity),
            "balanced_score": rounded(balanced),
            "pairwise_auc": rounded(pairwise_auc),
            "positive_median_rank": rounded(positive_median_rank),
            "negative_median_rank": rounded(negative_median_rank),
        },
        "criteria": criteria,
        "reason": (
            "동결 모델이 새 성공·실패 산업 홀드아웃 기준을 통과했습니다. 시장 미반영도와 수익률 검증 전에는 투자에 사용할 수 없습니다."
            if passed
            else "동결 모델이 새 홀드아웃의 재현율·허위경보·순위분리 기준 중 하나 이상을 통과하지 못했습니다."
        ),
        "scenarios": results,
        "known_limitations": [
            "모든 홀드아웃은 2021-12-31 단일 시점이므로 시점 다양성 검증이 추가로 필요합니다.",
            "OpenDART 한국 상장기업 코호트는 미국 원천수요를 완전히 대표하지 않습니다.",
            "arXiv 과거 검색결과는 완전한 당시 빈티지 보장이 제한됩니다.",
            "역사 라벨은 연구용 벤치마크이며 투자수익의 인과적 정답표가 아닙니다.",
            "주가 선반영도·거래비용·최대낙폭은 아직 포함되지 않았습니다.",
        ],
    }
