from __future__ import annotations

import math
import statistics
from typing import Any

from ible.analytics.scoring import _feature_to_score, _growth_features, clamp
from ible.models import Signal


def _weighted_mean(values: list[tuple[float, float]], default: float = 0.0) -> float:
    clean = [(v, w) for v, w in values if math.isfinite(v) and math.isfinite(w) and w > 0]
    total = sum(w for _, w in clean)
    return sum(v * w for v, w in clean) / total if total else default



def _weighted_quantile(values: list[tuple[float, float]], quantile: float) -> float:
    clean = sorted((float(v), float(w)) for v, w in values if math.isfinite(v) and math.isfinite(w) and w > 0)
    if not clean:
        return 0.0
    quantile = max(0.0, min(1.0, float(quantile)))
    total = sum(weight for _, weight in clean)
    threshold = quantile * total
    cumulative = 0.0
    for value, weight in clean:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return clean[-1][0]


def _robust_weighted_location(values: list[tuple[float, float]], default: float = 0.0) -> float:
    clean = [(float(v), float(w)) for v, w in values if math.isfinite(v) and math.isfinite(w) and w > 0]
    if not clean:
        return default
    if len(clean) < 3:
        return _weighted_mean(clean, default)
    lower = _weighted_quantile(clean, 0.20)
    upper = _weighted_quantile(clean, 0.80)
    clipped = [(min(upper, max(lower, value)), weight) for value, weight in clean]
    winsor_mean = _weighted_mean(clipped, default)
    median = _weighted_quantile(clean, 0.50)
    return 0.60 * median + 0.40 * winsor_mean

def effective_weight(profile: dict[str, Any]) -> float:
    exposure = max(0.0, min(1.0, float(profile.get("exposure", 0.0))))
    confidence = max(0.0, min(1.0, float(profile.get("confidence", 0.0))))
    return exposure * confidence


def build_exposure_weighted_signal(
    name: str,
    company_series: dict[str, list[tuple[str, float]]],
    profiles: dict[str, dict[str, Any]],
    minimum_exposure: float = 0.30,
) -> Signal:
    eligible = {
        ticker: profile
        for ticker, profile in profiles.items()
        if float(profile.get("exposure", 0.0)) >= minimum_exposure
    }
    features: dict[str, dict[str, float | None]] = {}
    weights: dict[str, float] = {}
    for ticker, profile in eligible.items():
        series = company_series.get(ticker, [])
        if not series:
            continue
        feature = _growth_features(series)
        if feature.get("yoy") is None:
            continue
        weight = effective_weight(profile)
        if weight <= 0:
            continue
        features[ticker] = feature
        weights[ticker] = weight

    total_requested_weight = sum(effective_weight(p) for p in eligible.values())
    total_usable_weight = sum(weights.values())
    coverage = total_usable_weight / total_requested_weight if total_requested_weight else 0.0
    concentration = max(weights.values()) / total_usable_weight if total_usable_weight else 1.0

    yoy_pairs = [(float(f["yoy"]), weights[t]) for t, f in features.items() if f["yoy"] is not None]
    accel_pairs = [(float(f["accel"]), weights[t]) for t, f in features.items() if f["accel"] is not None]
    level_pairs = [(float(f["level"]), weights[t]) for t, f in features.items() if f["level"] is not None]
    raw_yoy_mean = _weighted_mean(yoy_pairs)
    raw_accel_mean = _weighted_mean(accel_pairs)
    yoy = _robust_weighted_location(yoy_pairs)
    accel = _robust_weighted_location(accel_pairs)
    persistence = _weighted_mean([(float(f["persistence"]), weights[t]) for t, f in features.items() if f["persistence"] is not None], 0.5)
    level = _robust_weighted_location(level_pairs)
    positive_weight = sum(weights[t] for t, f in features.items() if float(f.get("yoy") or 0) > 0)
    breadth = positive_weight / total_usable_weight if total_usable_weight else 0.0

    level_score = _feature_to_score(level, 38.0)
    velocity_score = _feature_to_score(yoy, 34.0)
    acceleration_score = _feature_to_score(accel, 42.0)
    persistence_score = clamp(100.0 * persistence)
    breadth_score = clamp(100.0 * breadth)
    concentration_penalty = max(0.0, concentration - 0.45) * 35.0
    score = clamp(
        0.25 * level_score
        + 0.25 * velocity_score
        + 0.25 * acceleration_score
        + 0.15 * persistence_score
        + 0.10 * breadth_score
        - concentration_penalty
    )
    warnings: list[str] = []
    if coverage < 0.55:
        warnings.append("테마 노출도 기준을 통과한 기업의 재무 데이터 확보율이 낮습니다.")
    if concentration > 0.55:
        warnings.append("한 기업의 기여도가 과도해 집중도 감점을 적용했습니다.")

    return Signal(
        name=name,
        score=round(score, 2),
        level=round(level_score, 2),
        velocity=round(velocity_score, 2),
        acceleration=round(acceleration_score, 2),
        persistence=round(persistence_score, 2),
        breadth=round(breadth_score, 2),
        coverage=round(coverage, 4),
        raw={
            "eligible_company_count": len(eligible),
            "usable_company_count": len(features),
            "effective_weight_coverage": coverage,
            "largest_company_weight_share": concentration,
            "weighted_yoy": yoy,
            "weighted_acceleration": accel,
            "raw_weighted_yoy_mean": raw_yoy_mean,
            "raw_weighted_acceleration_mean": raw_accel_mean,
            "aggregation_method": "60pct_weighted_median_plus_40pct_20pct_winsor_mean",
            "companies": {
                ticker: {
                    "effective_weight": weights[ticker],
                    "exposure": profiles[ticker].get("exposure"),
                    "confidence": profiles[ticker].get("confidence"),
                    "evidence": profiles[ticker].get("evidence"),
                    "features": features[ticker],
                }
                for ticker in features
            },
        },
        warnings=warnings,
    )


def build_exposure_weighted_margin_signal(
    revenue_series: dict[str, list[tuple[str, float]]],
    profit_series: dict[str, list[tuple[str, float]]],
    profiles: dict[str, dict[str, Any]],
    minimum_exposure: float = 0.30,
) -> Signal:
    values: list[tuple[float, float]] = []
    positive_weight = 0.0
    total_weight = 0.0
    raw: dict[str, Any] = {}
    requested_weight = sum(
        effective_weight(p)
        for p in profiles.values()
        if float(p.get("exposure", 0)) >= minimum_exposure
    )
    for ticker, profile in profiles.items():
        if float(profile.get("exposure", 0)) < minimum_exposure:
            continue
        rev = dict(revenue_series.get(ticker, []))
        profit = dict(profit_series.get(ticker, []))
        common = sorted(set(rev) & set(profit))
        margins = [profit[d] / rev[d] for d in common if rev[d] > 0]
        margins = [value for value in margins if math.isfinite(value) and -2.0 <= value <= 2.0]
        if len(margins) < 5:
            continue
        latest = statistics.mean(margins[-2:])
        prior = statistics.mean(margins[-4:-2])
        delta = max(-0.50, min(0.50, latest - prior))
        weight = effective_weight(profile)
        company_score = clamp(50.0 + 700.0 * delta)
        values.append((company_score, weight))
        total_weight += weight
        if delta > 0:
            positive_weight += weight
        raw[ticker] = {"latest_margin": latest, "prior_margin": prior, "delta": delta, "weight": weight}
    breadth = positive_weight / total_weight if total_weight else 0.0
    raw_score = 0.75 * _weighted_mean(values, 50.0) + 0.25 * 100.0 * breadth
    coverage = total_weight / requested_weight if requested_weight else 0.0
    score = 50.0 + min(1.0, coverage) * (raw_score - 50.0)
    return Signal(
        name="exposure_weighted_margin",
        score=round(clamp(score), 2),
        breadth=round(100.0 * breadth, 2),
        coverage=round(coverage, 4),
        raw={"companies": raw, "raw_score_before_coverage_shrinkage": raw_score},
        warnings=(
            ["노출도 기준을 통과한 기업의 이익률 데이터가 부족합니다."]
            if not values
            else (["이익률 데이터 확보율이 낮아 점수를 중립값 방향으로 축소했습니다."] if coverage < 0.55 else [])
        ),
    )


def harmonic_mean(values: list[float]) -> float:
    clean = [max(1e-6, float(v)) for v in values if math.isfinite(v)]
    return len(clean) / sum(1.0 / v for v in clean) if clean else 0.0
