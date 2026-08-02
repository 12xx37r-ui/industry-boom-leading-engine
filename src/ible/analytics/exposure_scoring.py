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

    yoy = _weighted_mean([(float(f["yoy"]), weights[t]) for t, f in features.items() if f["yoy"] is not None])
    accel = _weighted_mean([(float(f["accel"]), weights[t]) for t, f in features.items() if f["accel"] is not None])
    persistence = _weighted_mean([(float(f["persistence"]), weights[t]) for t, f in features.items() if f["persistence"] is not None], 0.5)
    level = _weighted_mean([(float(f["level"]), weights[t]) for t, f in features.items() if f["level"] is not None])
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
        margins = [profit[d] / rev[d] for d in common if rev[d] != 0]
        if len(margins) < 5:
            continue
        latest = statistics.mean(margins[-2:])
        prior = statistics.mean(margins[-4:-2])
        delta = latest - prior
        weight = effective_weight(profile)
        company_score = clamp(50.0 + 700.0 * delta)
        values.append((company_score, weight))
        total_weight += weight
        if delta > 0:
            positive_weight += weight
        raw[ticker] = {"latest_margin": latest, "prior_margin": prior, "delta": delta, "weight": weight}
    breadth = positive_weight / total_weight if total_weight else 0.0
    score = 0.75 * _weighted_mean(values, 50.0) + 0.25 * 100.0 * breadth
    coverage = total_weight / requested_weight if requested_weight else 0.0
    return Signal(
        name="exposure_weighted_margin",
        score=round(clamp(score), 2),
        breadth=round(100.0 * breadth, 2),
        coverage=round(coverage, 4),
        raw=raw,
        warnings=[] if values else ["노출도 기준을 통과한 기업의 이익률 데이터가 부족합니다."],
    )


def harmonic_mean(values: list[float]) -> float:
    clean = [max(1e-6, float(v)) for v in values if math.isfinite(v)]
    return len(clean) / sum(1.0 / v for v in clean) if clean else 0.0
