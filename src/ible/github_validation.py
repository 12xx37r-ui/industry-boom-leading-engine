from __future__ import annotations

import gzip
import hashlib
import json
import statistics
from pathlib import Path
from typing import Any

from ible.model_lock import load_and_verify_model_lock


class ValidationPackError(RuntimeError):
    """Raised when the sealed validation pack is missing or altered."""


def _canonical_sha256(payload: Any) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def load_bundle(path: Path) -> dict[str, Any]:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ValidationPackError(f"validation bundle unavailable or invalid: {exc}") from exc

    expected = str(payload.get("content_sha256") or "")
    unsigned = dict(payload)
    unsigned.pop("content_sha256", None)
    actual = _canonical_sha256(unsigned)
    if not expected or expected != actual:
        raise ValidationPackError(
            f"validation bundle SHA-256 mismatch: expected={expected or 'missing'} actual={actual}"
        )

    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValidationPackError("validation bundle contains no cases")

    expected_case_hashes = dict(payload.get("case_hashes") or {})
    seen: set[str] = set()
    for case in cases:
        scenario_id = str(case.get("scenario_id") or "")
        if not scenario_id or scenario_id in seen:
            raise ValidationPackError(f"invalid or duplicate scenario_id: {scenario_id!r}")
        seen.add(scenario_id)
        actual_case_hash = _canonical_sha256(case)
        if expected_case_hashes.get(scenario_id) != actual_case_hash:
            raise ValidationPackError(f"case SHA-256 mismatch: {scenario_id}")
    if set(expected_case_hashes) != seen:
        raise ValidationPackError("case hash index does not match bundled scenarios")
    return payload


def _pairwise_auc(positives: list[dict[str, Any]], negatives: list[dict[str, Any]]) -> float | None:
    comparisons: list[float] = []
    for positive in positives:
        p_score = float((positive.get("observed") or {}).get("boom_score") or 0.0)
        for negative in negatives:
            n_score = float((negative.get("observed") or {}).get("boom_score") or 0.0)
            comparisons.append(1.0 if p_score > n_score else 0.5 if p_score == n_score else 0.0)
    return statistics.mean(comparisons) if comparisons else None


def run_github_validation(root: Path, output_dir: Path) -> dict[str, Any]:
    config_path = root / "config" / "github_validation.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    lock_result = load_and_verify_model_lock(root)
    bundle_path = root / str(config["bundle_file"])
    bundle = load_bundle(bundle_path)
    cases: list[dict[str, Any]] = list(bundle["cases"])

    eligible = [case for case in cases if case.get("status") != "INSUFFICIENT_DATA"]
    positives = [case for case in eligible if case.get("label") == "positive"]
    negatives = [case for case in eligible if case.get("label") == "negative"]

    positive_recall = (
        sum(bool(case.get("passed")) for case in positives) / len(positives)
        if positives
        else None
    )
    false_alarm_rate = (
        sum(bool(case.get("alert_triggered")) for case in negatives) / len(negatives)
        if negatives
        else None
    )
    alerts = [case for case in eligible if bool(case.get("alert_triggered"))]
    alert_precision = (
        sum(case.get("label") == "positive" for case in alerts) / len(alerts)
        if alerts
        else None
    )
    pairwise_auc = _pairwise_auc(positives, negatives)

    gates = dict(config.get("gates") or {})
    passed = bool(
        positive_recall is not None
        and positive_recall >= float(gates["positive_recall_min"])
        and false_alarm_rate is not None
        and false_alarm_rate <= float(gates["false_alarm_rate_max"])
        and pairwise_auc is not None
        and pairwise_auc >= float(gates["pairwise_auc_min"])
    )

    ranking = sorted(
        [
            {
                "scenario_id": case.get("scenario_id"),
                "scenario_name": case.get("scenario_name"),
                "label": case.get("label"),
                "status": case.get("status"),
                "passed": bool(case.get("passed")),
                "alert_triggered": bool(case.get("alert_triggered")),
                "as_of": case.get("as_of"),
                "target_theme_id": case.get("target_theme_id"),
                "boom_score": (case.get("observed") or {}).get("boom_score"),
                "early_signal_score": (case.get("observed") or {}).get("early_signal_score"),
                "rank": (case.get("observed") or {}).get("rank"),
                "stage": (case.get("observed") or {}).get("stage"),
                "data_confidence": (case.get("observed") or {}).get("data_confidence"),
            }
            for case in cases
        ],
        key=lambda row: float(row.get("boom_score") or -1.0),
        reverse=True,
    )

    summary = {
        "status": "V1_GITHUB_HISTORICAL_BENCHMARK_PASSED" if passed else "V1_GITHUB_HISTORICAL_BENCHMARK_FAILED",
        "engine_release": config.get("engine_release"),
        "frozen_model_version": bundle.get("frozen_model_version"),
        "execution_mode": "github_actions_only",
        "network_collection_used": False,
        "bat_cmd_colab_used": False,
        "investment_use_allowed": False,
        "validation_role": bundle.get("bundle_role"),
        "independent_external_holdout": {
            "status": "NOT_RUN",
            "reason": "봉인된 신규 점시점 외부 데이터셋이 저장소에 없으므로 기존 사례를 독립 홀드아웃으로 허위 표시하지 않습니다.",
        },
        "model_lock": lock_result,
        "bundle": {
            "file": str(config["bundle_file"]),
            "content_sha256": bundle.get("content_sha256"),
            "case_count": len(cases),
            "eligible_case_count": len(eligible),
            "insufficient_data_case_count": len(cases) - len(eligible),
        },
        "metrics": {
            "positive_cases": len(positives),
            "negative_cases": len(negatives),
            "positive_recall": round(positive_recall, 4) if positive_recall is not None else None,
            "false_alarm_rate": round(false_alarm_rate, 4) if false_alarm_rate is not None else None,
            "alert_precision": round(alert_precision, 4) if alert_precision is not None else None,
            "pairwise_auc": round(pairwise_auc, 4) if pairwise_auc is not None else None,
        },
        "criteria": gates,
        "ranking": ranking,
        "limitations": [
            "이 번들은 V0.9.1 개발 당시 이미 존재하던 역사 사례를 묶은 잠금 재현검증입니다.",
            "따라서 독립 외부 홀드아웃 통과로 간주하지 않습니다.",
            "신규 외부 점시점 데이터가 확보되기 전까지 실전 투자 사용은 금지됩니다.",
        ],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "v1_github_validation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "v1_github_validation_ranking.json").write_text(
        json.dumps(ranking, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "v1_model_lock_verification.json").write_text(
        json.dumps(lock_result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary
