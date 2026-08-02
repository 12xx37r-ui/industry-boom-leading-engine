from __future__ import annotations

import gzip
import hashlib
import json
import statistics
from datetime import date
from pathlib import Path
from typing import Any

from ible.backtest import evaluate_scenario
from ible.github_validation import load_bundle
from ible.model_lock import load_and_verify_model_lock


class BlindHoldoutError(RuntimeError):
    """Raised when the sealed blind holdout pack is missing or altered."""


def _canonical_sha256(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def load_blind_pack(path: Path) -> dict[str, Any]:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        raise BlindHoldoutError(f"blind holdout pack unavailable or invalid: {exc}") from exc

    expected = str(payload.get("content_sha256") or "")
    unsigned = dict(payload)
    unsigned.pop("content_sha256", None)
    actual = _canonical_sha256(unsigned)
    if not expected or expected != actual:
        raise BlindHoldoutError(
            f"blind holdout pack SHA-256 mismatch: expected={expected or 'missing'} actual={actual}"
        )

    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise BlindHoldoutError("blind holdout pack contains no cases")

    expected_case_hashes = dict(payload.get("case_hashes") or {})
    seen: set[str] = set()
    source_pairs: set[tuple[str, str]] = set()
    for case in cases:
        case_id = str(case.get("id") or "")
        if not case_id or case_id in seen:
            raise BlindHoldoutError(f"invalid or duplicate blind case id: {case_id!r}")
        seen.add(case_id)
        actual_hash = _canonical_sha256(case)
        if expected_case_hashes.get(case_id) != actual_hash:
            raise BlindHoldoutError(f"blind case SHA-256 mismatch: {case_id}")
        pair = (str(case.get("source_scenario_id") or ""), str(case.get("target_theme_id") or ""))
        if not all(pair) or pair in source_pairs:
            raise BlindHoldoutError(f"invalid or duplicate source-target pair: {pair}")
        source_pairs.add(pair)
    if set(expected_case_hashes) != seen:
        raise BlindHoldoutError("blind case hash index does not match bundled cases")
    return payload


def _pairwise_auc(positives: list[dict[str, Any]], negatives: list[dict[str, Any]]) -> float | None:
    comparisons: list[float] = []
    for positive in positives:
        p_score = float((positive.get("observed") or {}).get("boom_score") or 0.0)
        for negative in negatives:
            n_score = float((negative.get("observed") or {}).get("boom_score") or 0.0)
            comparisons.append(1.0 if p_score > n_score else 0.5 if p_score == n_score else 0.0)
    return statistics.mean(comparisons) if comparisons else None


def _months_between(start: str, end: str) -> int | None:
    if not start or not end:
        return None
    a = date.fromisoformat(start)
    b = date.fromisoformat(end)
    return (b.year - a.year) * 12 + (b.month - a.month)


def run_blind_holdout(root: Path, output_dir: Path) -> dict[str, Any]:
    model_lock = load_and_verify_model_lock(root)
    history = load_bundle(root / "validation_seed" / "v1_locked_backtests.json.gz")
    blind = load_blind_pack(root / "validation_seed" / "v1_blind_theme_holdout.json.gz")

    history_by_id = {str(case["scenario_id"]): case for case in history["cases"]}
    results: list[dict[str, Any]] = []
    for sealed in blind["cases"]:
        source_id = str(sealed["source_scenario_id"])
        source = history_by_id.get(source_id)
        if source is None:
            raise BlindHoldoutError(f"source scenario missing from historical pack: {source_id}")
        if str(source.get("target_theme_id")) == str(sealed["target_theme_id"]):
            raise BlindHoldoutError(
                f"blind case reuses the source scenario target and is not blind: {sealed['id']}"
            )
        scenario = {
            "id": sealed["id"],
            "name": sealed["name"],
            "label": sealed["label"],
            "as_of": source["as_of"],
            "target_theme_id": sealed["target_theme_id"],
            "comparison_theme_ids": source.get("comparison_theme_ids") or [],
            "criteria": sealed["criteria"],
        }
        evaluated = evaluate_scenario(scenario, list(source["ranking"]))
        evaluated["source_scenario_id"] = source_id
        evaluated["source_original_target_theme_id"] = source.get("target_theme_id")
        evaluated["outcome_class"] = sealed.get("outcome_class")
        evaluated["boom_start"] = sealed.get("boom_start")
        evaluated["lead_time_months"] = _months_between(str(source["as_of"]), str(sealed.get("boom_start") or ""))
        results.append(evaluated)

    eligible = [row for row in results if row.get("status") != "INSUFFICIENT_DATA"]
    positives = [row for row in eligible if row.get("label") == "positive"]
    negatives = [row for row in eligible if row.get("label") == "negative"]
    recall = sum(bool(row.get("passed")) for row in positives) / len(positives) if positives else None
    false_alarm = (
        sum(bool(row.get("alert_triggered")) for row in negatives) / len(negatives)
        if negatives else None
    )
    alerts = [row for row in eligible if bool(row.get("alert_triggered"))]
    precision = (
        sum(row.get("label") == "positive" for row in alerts) / len(alerts)
        if alerts else None
    )
    auc = _pairwise_auc(positives, negatives)
    successful_leads = [
        float(row["lead_time_months"])
        for row in positives
        if row.get("passed") and row.get("lead_time_months") is not None
    ]
    median_lead = statistics.median(successful_leads) if successful_leads else None
    gates = dict(blind.get("gates") or {})
    passed = bool(
        recall is not None
        and recall >= float(gates["positive_recall_min"])
        and false_alarm is not None
        and false_alarm <= float(gates["false_alarm_rate_max"])
        and auc is not None
        and auc >= float(gates["pairwise_auc_min"])
    )

    ranking = sorted(
        [
            {
                "case_id": row.get("scenario_id"),
                "case_name": row.get("scenario_name"),
                "label": row.get("label"),
                "status": row.get("status"),
                "passed": bool(row.get("passed")),
                "alert_triggered": bool(row.get("alert_triggered")),
                "as_of": row.get("as_of"),
                "source_scenario_id": row.get("source_scenario_id"),
                "target_theme_id": row.get("target_theme_id"),
                "boom_score": (row.get("observed") or {}).get("boom_score"),
                "early_signal_score": (row.get("observed") or {}).get("early_signal_score"),
                "rank": (row.get("observed") or {}).get("rank"),
                "stage": (row.get("observed") or {}).get("stage"),
                "data_confidence": (row.get("observed") or {}).get("data_confidence"),
                "lead_time_months": row.get("lead_time_months"),
            }
            for row in results
        ],
        key=lambda row: float(row.get("boom_score") or -1.0),
        reverse=True,
    )

    summary = {
        "status": "V1_1_BLIND_THEME_HOLDOUT_PASSED" if passed else "V1_1_BLIND_THEME_HOLDOUT_FAILED",
        "engine_release": blind.get("engine_release"),
        "frozen_model_version": blind.get("frozen_model_version"),
        "execution_mode": "github_actions_only",
        "network_collection_used": False,
        "bat_cmd_colab_used": False,
        "investment_use_allowed": False,
        "validation_role": blind.get("bundle_role"),
        "external_independence": False,
        "independent_external_holdout": {
            "status": "NOT_RUN",
            "reason": "이번 단계는 기존 점시점 스냅샷 안에서 개발 목표가 아니었던 산업·날짜 조합을 봉인한 블라인드 검증입니다. 신규 외부 데이터 검증은 아닙니다.",
        },
        "model_lock": model_lock,
        "source_bundle": {
            "file": blind.get("source_bundle"),
            "content_sha256": history.get("content_sha256"),
            "case_count": len(history["cases"]),
        },
        "blind_pack": {
            "file": "validation_seed/v1_blind_theme_holdout.json.gz",
            "content_sha256": blind.get("content_sha256"),
            "sealed_at": blind.get("sealed_at"),
            "case_count": len(results),
            "eligible_case_count": len(eligible),
        },
        "metrics": {
            "positive_cases": len(positives),
            "negative_cases": len(negatives),
            "positive_recall": round(recall, 4) if recall is not None else None,
            "false_alarm_rate": round(false_alarm, 4) if false_alarm is not None else None,
            "alert_precision": round(precision, 4) if precision is not None else None,
            "pairwise_auc": round(auc, 4) if auc is not None else None,
            "median_successful_lead_months": round(median_lead, 1) if median_lead is not None else None,
        },
        "criteria": gates,
        "ranking": ranking,
        "limitations": [
            "V0.9.1 계산식과 판정식은 SHA-256 잠금 상태로 유지됩니다.",
            "각 블라인드 사례는 해당 원본 시나리오의 개발 목표 산업과 다른 산업을 평가합니다.",
            "그러나 재무·연구 원천 스냅샷은 기존 번들과 같으므로 외부 독립 홀드아웃으로 간주하지 않습니다.",
            "신규 외부 점시점 데이터와 미래 실시간 섀도 검증 전까지 투자 사용을 금지합니다.",
        ],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "v1_1_blind_holdout_summary.json": summary,
        "v1_1_blind_holdout_ranking.json": ranking,
        "v1_1_blind_holdout_scenarios.json": results,
        "v1_1_model_lock_verification.json": model_lock,
        "v1_1_next_gate.json": {
            "current_gate": summary["status"],
            "next_required_gate": "NEW_EXTERNAL_POINT_IN_TIME_DATASET_OR_PROSPECTIVE_SHADOW_WINDOW",
            "investment_use_allowed": False,
        },
    }
    for name, payload in outputs.items():
        (output_dir / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
