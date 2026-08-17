from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from ible.integrity import canonical_sha256, load_json, write_json


FEATURES = ("revenue_growth", "employment_growth", "capex_growth", "stock_return", "industry_growth")


def _finite(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _safe_mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _correlation(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    mx, my = _safe_mean(xs), _safe_mean(ys)
    if mx is None or my is None:
        return None
    numerator = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return numerator / (dx * dy) if dx and dy else None


def _normalise(weights: dict[str, float]) -> dict[str, float]:
    clipped = {key: max(0.0, float(weights.get(key, 0.0))) for key in FEATURES}
    total = sum(clipped.values())
    if total <= 0:
        return {key: round(1.0 / len(FEATURES), 6) for key in FEATURES}
    return {key: round(clipped[key] / total, 6) for key in FEATURES}


def _load_v8(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    scorecard_path = root / "outputs/v8_layers/v8_validation_scorecard.json"
    layer_path = root / "outputs/v8_layers/v8_current_layer_snapshot.json"
    if not scorecard_path.is_file() or not layer_path.is_file():
        raise RuntimeError("V8 outputs are required before V9 learning")
    return load_json(scorecard_path), load_json(layer_path)


def _matured_rows(scorecard: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for horizon in (scorecard.get("horizons") or {}).values():
        for evaluation in horizon.get("evaluations") or []:
            rows.extend(evaluation.get("themes") or [])
    return rows


def _learn(rows: list[dict[str, Any]], baseline: dict[str, float]) -> dict[str, Any]:
    correlations: dict[str, float | None] = {}
    for feature in FEATURES:
        xs, ys = [], []
        for row in rows:
            value = _finite((row.get("component_scores") or {}).get(feature))
            outcome = _finite(row.get("realized_outcome_score"))
            if value is not None and outcome is not None:
                xs.append(value)
                ys.append(outcome)
        correlations[feature] = None if len(xs) < 3 else round(_correlation(xs, ys) or 0.0, 6)
    positive_signal = {key: max(0.0, value or 0.0) for key, value in correlations.items()}
    candidate = _normalise(positive_signal)
    return {"candidate_weights": candidate, "feature_correlations": correlations, "usable_feature_counts": {feature: sum((row.get("component_scores") or {}).get(feature) is not None for row in rows) for feature in FEATURES}}


def run_v9(root: Path, output_dir: Path, run_date: str | None = None) -> dict[str, Any]:
    config = load_json(root / "config/v9_learning.json")
    scorecard, layer = _load_v8(root)
    rows = _matured_rows(scorecard)
    baseline = _normalise({key: float(value) for key, value in (config.get("baseline_weights") or {}).items()})
    learned = _learn(rows, baseline) if rows else {"candidate_weights": baseline, "feature_correlations": {key: None for key in FEATURES}, "usable_feature_counts": {key: 0 for key in FEATURES}}
    maturity = {
        str(horizon): int(((scorecard.get("horizons") or {}).get(str(horizon)) or {}).get("matured_snapshot_count", 0))
        for horizon in (6, 12, 24)
    }
    minimums = config.get("minimum_matured_snapshots") or {}
    observation_gate = len(rows) >= int(config.get("minimum_observations", 100))
    horizon_gate = all(maturity[key] >= int(minimums.get(key, 0)) for key in ("6", "12", "24"))
    evidence_sufficient = observation_gate and horizon_gate
    shrinkage = float(config.get("shrinkage_to_baseline", 0.75))
    candidate = learned["candidate_weights"]
    blended = _normalise({key: shrinkage * baseline[key] + (1.0 - shrinkage) * candidate[key] for key in FEATURES}) if evidence_sufficient else baseline
    core = {
        "schema_version": 1,
        "engine_release": config["engine_release"],
        "as_of": run_date,
        "source_v8_layer_sha256": layer.get("content_sha256"),
        "observation_count": len(rows),
        "matured_snapshot_counts": maturity,
        "baseline_weights": baseline,
        "raw_candidate_weights": candidate,
        "blended_candidate_weights": blended,
        "feature_correlations": learned["feature_correlations"],
        "usable_feature_counts": learned["usable_feature_counts"],
        "evidence_sufficient": evidence_sufficient,
        "automatic_promotion_allowed": False,
        "promotion_status": "LEARNING_CANDIDATE_READY_FOR_REVIEW" if evidence_sufficient else "WAITING_FOR_MATURE_OUTCOMES",
        "investment_use_allowed": False,
    }
    core["content_sha256"] = canonical_sha256(core)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "v9_weight_candidate.json", core)
    summary = {
        "status": "V9_LEARNING_CANDIDATE_READY" if evidence_sufficient else "V9_LEARNING_ACCUMULATING",
        "engine_release": config["engine_release"],
        "as_of": run_date,
        "observation_count": len(rows),
        "matured_snapshot_counts": maturity,
        "evidence_sufficient": evidence_sufficient,
        "automatic_promotion_allowed": False,
        "promotion_status": core["promotion_status"],
        "investment_use_allowed": False,
    }
    dashboard = {"status": summary["status"], "as_of": run_date, "learning": core, "locked_v7_v8_scores_unchanged": True, "investment_use_allowed": False}
    write_json(output_dir / "v9_run_summary.json", summary)
    write_json(output_dir / "v9_learning_dashboard.json", dashboard)
    write_json(output_dir / "v9_next_gate.json", {"current_status": summary["status"], "next_required_gate": "ACCUMULATE_MATURE_6_12_24_MONTH_OUTCOMES_AND_REVIEW_WEIGHT_CANDIDATE", "automatic_promotion_allowed": False, "investment_use_allowed": False})
    return summary
