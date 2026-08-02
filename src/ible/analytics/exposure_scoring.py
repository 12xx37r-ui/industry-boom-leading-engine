from __future__ import annotations

import datetime as dt
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


def _metric_kind(name: str) -> str:
    lowered = str(name or "").lower()
    for metric in ("revenue", "capex", "rd"):
        if metric in lowered:
            return metric
    return "generic"


def _trailing_pair(series: list[tuple[str, float]]) -> tuple[float, float] | None:
    clean = [float(value) for _, value in series if math.isfinite(float(value))]
    if len(clean) < 8:
        return None
    current = sum(clean[-4:])
    prior = sum(clean[-8:-4])
    if prior == 0:
        return None
    return current, prior


def _scale_score(metric: str, exposed_trailing_value: float) -> float:
    """Convert exposed annual flow dollars into a smooth economic-scale score.

    Growth alone makes tiny low-base companies look like mature capital cycles.  The
    score is deliberately smooth rather than a hard minimum so emerging industries
    are not excluded, but percentage spikes cannot receive full weight without
    meaningful dollars behind them.
    """
    thresholds = {
        "revenue": 2_000_000_000.0,
        "capex": 200_000_000.0,
        "rd": 200_000_000.0,
        "generic": 500_000_000.0,
    }
    threshold = thresholds.get(metric, thresholds["generic"])
    value = max(1.0, float(exposed_trailing_value))
    return clamp(50.0 + 18.0 * math.log10(value / threshold))


def build_exposure_weighted_signal(
    name: str,
    company_series: dict[str, list[tuple[str, float]]],
    profiles: dict[str, dict[str, Any]],
    minimum_exposure: float = 0.30,
) -> Signal:
    metric = _metric_kind(name)
    eligible = {
        ticker: profile
        for ticker, profile in profiles.items()
        if float(profile.get("exposure", 0.0)) >= minimum_exposure
    }
    features: dict[str, dict[str, float | None]] = {}
    weights: dict[str, float] = {}
    trailing: dict[str, dict[str, float]] = {}
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
        pair = _trailing_pair(series)
        if pair is not None:
            current, prior = pair
            trailing[ticker] = {"current": current, "prior": prior}

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
    persistence = _weighted_mean(
        [(float(f["persistence"]), weights[t]) for t, f in features.items() if f["persistence"] is not None],
        0.5,
    )
    level = _robust_weighted_location(level_pairs)
    positive_weight = sum(weights[t] for t, f in features.items() if float(f.get("yoy") or 0) > 0)
    breadth = positive_weight / total_usable_weight if total_usable_weight else 0.0

    level_score = _feature_to_score(level, 38.0)
    velocity_score = _feature_to_score(yoy, 34.0)
    acceleration_score = _feature_to_score(accel, 42.0)
    persistence_score = clamp(100.0 * persistence)
    breadth_score = clamp(100.0 * breadth)
    concentration_penalty = max(0.0, concentration - 0.45) * 35.0
    company_growth_score = clamp(
        0.25 * level_score
        + 0.25 * velocity_score
        + 0.25 * acceleration_score
        + 0.15 * persistence_score
        + 0.10 * breadth_score
        - concentration_penalty
    )

    aggregate_current = sum(row["current"] * weights[ticker] for ticker, row in trailing.items())
    aggregate_prior = sum(row["prior"] * weights[ticker] for ticker, row in trailing.items())
    aggregate_yoy = (
        (aggregate_current - aggregate_prior) / abs(aggregate_prior)
        if aggregate_prior != 0
        else None
    )
    aggregate_growth_score = _feature_to_score(aggregate_yoy, 34.0)
    economic_scale_score = _scale_score(metric, aggregate_current) if aggregate_current > 0 else 0.0

    # Company-level growth remains useful for breadth, but aggregate dollars and
    # economic scale now carry 55% of the signal.  This prevents three tiny firms
    # with 10x low-base CAPEX from outranking a broad, large real-economy cycle.
    score = clamp(
        0.45 * company_growth_score
        + 0.35 * aggregate_growth_score
        + 0.20 * economic_scale_score
    )
    low_base_spike_penalty = 0.0
    if yoy > 1.0 and economic_scale_score < 48.0:
        low_base_spike_penalty = min(18.0, 2.5 * (yoy - 1.0) + 0.30 * (48.0 - economic_scale_score))
        score = clamp(score - low_base_spike_penalty)

    warnings: list[str] = []
    if coverage < 0.55:
        warnings.append("테마 노출도 기준을 통과한 기업의 재무 데이터 확보율이 낮습니다.")
    if concentration > 0.55:
        warnings.append("한 기업의 기여도가 과도해 집중도 감점을 적용했습니다.")
    if low_base_spike_penalty > 0:
        warnings.append("절대 자금규모가 작은 저기저 급증에 감점을 적용했습니다.")

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
            "metric": metric,
            "eligible_company_count": len(eligible),
            "usable_company_count": len(features),
            "effective_weight_coverage": coverage,
            "largest_company_weight_share": concentration,
            "weighted_yoy": yoy,
            "weighted_acceleration": accel,
            "raw_weighted_yoy_mean": raw_yoy_mean,
            "raw_weighted_acceleration_mean": raw_accel_mean,
            "company_growth_score": company_growth_score,
            "aggregate_exposure_weighted_trailing_value": aggregate_current,
            "aggregate_exposure_weighted_prior_value": aggregate_prior,
            "aggregate_yoy": aggregate_yoy,
            "aggregate_growth_score": aggregate_growth_score,
            "economic_scale_score": economic_scale_score,
            "low_base_spike_penalty": low_base_spike_penalty,
            "aggregation_method": "45pct_company_robust_growth_plus_35pct_aggregate_dollar_growth_plus_20pct_scale",
            "companies": {
                ticker: {
                    "effective_weight": weights[ticker],
                    "exposure": profiles[ticker].get("exposure"),
                    "confidence": profiles[ticker].get("confidence"),
                    "evidence": profiles[ticker].get("evidence"),
                    "features": features[ticker],
                    "trailing_flow": trailing.get(ticker),
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
    rejected_implausible = 0
    requested_weight = sum(
        effective_weight(profile)
        for profile in profiles.values()
        if float(profile.get("exposure", 0)) >= minimum_exposure
    )
    for ticker, profile in profiles.items():
        if float(profile.get("exposure", 0)) < minimum_exposure:
            continue
        revenue = {str(date): float(value) for date, value in revenue_series.get(ticker, [])}
        profit = {str(date): float(value) for date, value in profit_series.get(ticker, [])}
        common = sorted(set(revenue) & set(profit))
        dated_margins: list[tuple[str, float]] = []
        for date in common:
            rev = revenue[date]
            gp = profit[date]
            if not math.isfinite(rev) or not math.isfinite(gp) or rev <= 0:
                continue
            margin = gp / rev
            if not -1.0 <= margin <= 1.02:
                rejected_implausible += 1
                continue
            dated_margins.append((date, margin))
        if len(dated_margins) < 5:
            continue
        quarter_like = 0
        for index in range(1, len(dated_margins)):
            try:
                current = dt.date.fromisoformat(dated_margins[index][0])
                prior = dt.date.fromisoformat(dated_margins[index - 1][0])
            except ValueError:
                continue
            if 45 <= (current - prior).days <= 140:
                quarter_like += 1
        if quarter_like < 4:
            continue
        margins = [margin for _, margin in dated_margins]
        latest = statistics.mean(margins[-2:])
        prior = statistics.mean(margins[-4:-2])
        delta = max(-0.20, min(0.20, latest - prior))
        weight = effective_weight(profile)
        company_score = clamp(50.0 + 250.0 * delta)
        values.append((company_score, weight))
        total_weight += weight
        if delta > 0:
            positive_weight += weight
        raw[ticker] = {
            "latest_margin": latest,
            "prior_margin": prior,
            "delta": delta,
            "weight": weight,
        }
    breadth = positive_weight / total_weight if total_weight else 0.0
    raw_score = 0.75 * _weighted_mean(values, 50.0) + 0.25 * 100.0 * breadth
    coverage = total_weight / requested_weight if requested_weight else 0.0
    score = 50.0 + min(1.0, coverage) * (raw_score - 50.0)
    warnings: list[str] = []
    if not values:
        warnings.append("노출도 기준을 통과한 기업의 정상 범위 매출총이익률 데이터가 부족합니다.")
    elif coverage < 0.55:
        warnings.append("매출총이익률 데이터 확보율이 낮아 점수를 중립값 방향으로 축소했습니다.")
    if rejected_implausible:
        warnings.append("매출과 불일치하는 비정상 매출총이익률 관측치를 제외했습니다.")
    return Signal(
        name="exposure_weighted_gross_margin",
        score=round(clamp(score), 2),
        breadth=round(100.0 * breadth, 2),
        coverage=round(coverage, 4),
        raw={
            "companies": raw,
            "raw_score_before_coverage_shrinkage": raw_score,
            "rejected_implausible_margin_points": rejected_implausible,
        },
        warnings=warnings,
    )


def build_operating_viability_signal(
    revenue_series: dict[str, list[tuple[str, float]]],
    operating_income_series: dict[str, list[tuple[str, float]]],
    profiles: dict[str, dict[str, Any]],
    minimum_exposure: float = 0.30,
) -> Signal:
    """Score operating economics, not merely whether spending is accelerating."""
    values: list[tuple[float, float]] = []
    total_weight = 0.0
    improving_weight = 0.0
    raw: dict[str, Any] = {}
    rejected_implausible = 0
    requested_weight = sum(
        effective_weight(profile)
        for profile in profiles.values()
        if float(profile.get("exposure", 0)) >= minimum_exposure
    )
    for ticker, profile in profiles.items():
        if float(profile.get("exposure", 0)) < minimum_exposure:
            continue
        revenue = {str(date): float(value) for date, value in revenue_series.get(ticker, [])}
        operating = {str(date): float(value) for date, value in operating_income_series.get(ticker, [])}
        common = sorted(set(revenue) & set(operating))
        margins: list[tuple[str, float]] = []
        for date in common:
            rev = revenue[date]
            op = operating[date]
            if not math.isfinite(rev) or not math.isfinite(op) or rev <= 0:
                continue
            margin = op / rev
            # Deeply loss-making early firms are valid observations.  Only values
            # beyond this range are almost certainly period/tag mismatches.
            if not -5.0 <= margin <= 1.0:
                rejected_implausible += 1
                continue
            margins.append((date, margin))
        if len(margins) < 5:
            continue
        quarter_like = 0
        for index in range(1, len(margins)):
            try:
                current = dt.date.fromisoformat(margins[index][0])
                prior = dt.date.fromisoformat(margins[index - 1][0])
            except ValueError:
                continue
            if 45 <= (current - prior).days <= 140:
                quarter_like += 1
        if quarter_like < 4:
            continue
        latest = statistics.mean(value for _, value in margins[-2:])
        prior = statistics.mean(value for _, value in margins[-4:-2])
        delta = max(-0.50, min(0.50, latest - prior))
        level_score = clamp(50.0 + 38.0 * math.tanh(2.2 * latest))
        trend_score = clamp(50.0 + 32.0 * math.tanh(4.0 * delta))
        company_score = 0.72 * level_score + 0.28 * trend_score
        weight = effective_weight(profile)
        values.append((company_score, weight))
        total_weight += weight
        if delta >= 0:
            improving_weight += weight
        raw[ticker] = {
            "latest_operating_margin": latest,
            "prior_operating_margin": prior,
            "delta": delta,
            "level_score": level_score,
            "trend_score": trend_score,
            "company_score": company_score,
            "weight": weight,
        }

    coverage = total_weight / requested_weight if requested_weight else 0.0
    breadth = improving_weight / total_weight if total_weight else 0.0
    robust_company_score = _robust_weighted_location(values, 50.0)
    raw_score = 0.85 * robust_company_score + 0.15 * 100.0 * breadth
    score = 50.0 + min(1.0, coverage) * (raw_score - 50.0)
    warnings: list[str] = []
    if not values:
        warnings.append("정상 범위 영업이익률 데이터가 부족해 중립값을 사용했습니다.")
    elif coverage < 0.55:
        warnings.append("영업이익률 데이터 확보율이 낮아 점수를 중립값 방향으로 축소했습니다.")
    if rejected_implausible:
        warnings.append("매출과 기간이 맞지 않는 비정상 영업이익률 관측치를 제외했습니다.")
    return Signal(
        name="operating_viability",
        score=round(clamp(score), 2),
        level=round(clamp(robust_company_score), 2),
        breadth=round(100.0 * breadth, 2),
        coverage=round(coverage, 4),
        raw={
            "companies": raw,
            "raw_score_before_coverage_shrinkage": raw_score,
            "rejected_implausible_margin_points": rejected_implausible,
        },
        warnings=warnings,
    )


def harmonic_mean(values: list[float]) -> float:
    clean = [max(1e-6, float(v)) for v in values if math.isfinite(v)]
    return len(clean) / sum(1.0 / v for v in clean) if clean else 0.0
