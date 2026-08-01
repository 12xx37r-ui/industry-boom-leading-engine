from __future__ import annotations

import math
import statistics
from typing import Any

from ible.analytics.timeseries import clamp, safe_growth, slope, z_to_score
from ible.models import Signal, ThemeResult


def _mean(values: list[float], default: float = 50.0) -> float:
    clean = [v for v in values if math.isfinite(v)]
    return statistics.mean(clean) if clean else default


def _growth_features(series: list[tuple[str, float]]) -> dict[str, float | None]:
    values = [v for _, v in series]
    if len(values) < 5:
        return {"yoy": None, "accel": None, "persistence": None, "level": None}
    yoy_values: list[float] = []
    for index in range(4, len(values)):
        growth = safe_growth(values[index], values[index - 4])
        if growth is not None:
            yoy_values.append(growth)
    if not yoy_values:
        return {"yoy": None, "accel": None, "persistence": None, "level": None}
    recent_yoy = yoy_values[-1]
    prior_yoy = yoy_values[-2] if len(yoy_values) > 1 else 0.0
    accel = recent_yoy - prior_yoy
    sequential = [safe_growth(values[i], values[i - 1]) for i in range(1, len(values))]
    recent_seq = [x for x in sequential[-6:] if x is not None]
    persistence = sum(1 for x in recent_seq if x > 0) / len(recent_seq) if recent_seq else None
    recent_mean = _mean(values[-4:], 0.0)
    older_mean = _mean(values[-8:-4], recent_mean) if len(values) >= 8 else recent_mean
    level = safe_growth(recent_mean, older_mean)
    return {"yoy": recent_yoy, "accel": accel, "persistence": persistence, "level": level}


def _feature_to_score(value: float | None, scale: float, center: float = 50.0) -> float:
    if value is None or not math.isfinite(value):
        return 50.0
    return clamp(center + scale * math.tanh(value))


def build_metric_signal(name: str, company_series: dict[str, list[tuple[str, float]]]) -> Signal:
    features = {ticker: _growth_features(series) for ticker, series in company_series.items() if series}
    yoy = [f["yoy"] for f in features.values() if f["yoy"] is not None]
    accel = [f["accel"] for f in features.values() if f["accel"] is not None]
    persistence = [f["persistence"] for f in features.values() if f["persistence"] is not None]
    levels = [f["level"] for f in features.values() if f["level"] is not None]
    breadth = sum(1 for value in yoy if value > 0) / len(yoy) if yoy else 0.0
    level_score = _feature_to_score(_mean(levels, 0.0), 38.0)
    velocity_score = _feature_to_score(_mean(yoy, 0.0), 34.0)
    acceleration_score = _feature_to_score(_mean(accel, 0.0), 42.0)
    persistence_score = clamp(100.0 * _mean(persistence, 0.5))
    breadth_score = clamp(100.0 * breadth)
    coverage = len(features) / max(1, len(company_series))
    score = (
        0.25 * level_score
        + 0.25 * velocity_score
        + 0.25 * acceleration_score
        + 0.15 * persistence_score
        + 0.10 * breadth_score
    )
    warnings: list[str] = []
    if coverage < 0.5:
        warnings.append("기업 데이터 확보율이 50% 미만입니다.")
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
            "company_count": len(company_series),
            "usable_company_count": len(features),
            "median_yoy": statistics.median(yoy) if yoy else None,
            "median_acceleration": statistics.median(accel) if accel else None,
            "positive_breadth": breadth if yoy else None,
        },
        warnings=warnings,
    )


def build_margin_signal(
    revenue_series: dict[str, list[tuple[str, float]]],
    gross_profit_series: dict[str, list[tuple[str, float]]],
) -> Signal:
    scores: list[float] = []
    positive = 0
    usable = 0
    raw: dict[str, Any] = {}
    for ticker, revenues in revenue_series.items():
        gp = gross_profit_series.get(ticker, [])
        revenue_by_date = dict(revenues)
        gp_by_date = dict(gp)
        common = sorted(set(revenue_by_date) & set(gp_by_date))
        margins = [gp_by_date[d] / revenue_by_date[d] for d in common if revenue_by_date[d] != 0]
        if len(margins) < 5:
            continue
        usable += 1
        latest = statistics.mean(margins[-2:])
        prior = statistics.mean(margins[-4:-2])
        delta = latest - prior
        company_score = clamp(50.0 + 700.0 * delta)
        scores.append(company_score)
        if delta > 0:
            positive += 1
        raw[ticker] = {"latest_margin": latest, "prior_margin": prior, "delta": delta}
    breadth = positive / usable if usable else 0.0
    score = 0.75 * _mean(scores) + 0.25 * 100.0 * breadth if usable else 50.0
    return Signal(
        name="pricing_power_margin",
        score=round(score, 2),
        breadth=round(100.0 * breadth, 2),
        coverage=round(usable / max(1, len(revenue_series)), 4),
        raw=raw,
        warnings=[] if usable else ["매출·매출총이익 공통 분기 데이터가 부족합니다."],
    )


def stage_for(score: float, confidence: float, persistence: float, breadth: float) -> str:
    if confidence < 50:
        return "INSUFFICIENT_DATA"
    if score >= 78 and persistence >= 62 and breadth >= 58:
        return "PRE_BOOM"
    if score >= 67:
        return "ACCUMULATION"
    if score >= 56:
        return "WATCH"
    return "NO_SIGNAL"


def probability(score: float, horizon: int, confidence: float) -> float:
    # Calibrated only as an initial monotonic mapping. Backtest calibration must replace this.
    horizon_shift = {6: -7.0, 12: 0.0, 24: 4.0}[horizon]
    x = (score + horizon_shift - 60.0) / 10.0
    raw = 1.0 / (1.0 + math.exp(-x))
    reliability = 0.5 + 0.5 * confidence / 100.0
    return round(max(0.02, min(0.98, raw * reliability)), 4)


def build_theme_result(
    *,
    theme_id: str,
    theme_name: str,
    as_of: str,
    signals: dict[str, Signal],
    requested_companies: int,
    usable_companies: int,
    invalidations: list[str],
) -> ThemeResult:
    capital = signals["capex"]
    rd = signals["rd"]
    demand = signals["revenue"]
    margin = signals["margin"]
    capital_engine = 0.65 * capital.score + 0.35 * rd.score
    breadth_engine = _mean([capital.breadth, rd.breadth, demand.breadth])
    persistence_engine = _mean([capital.persistence, rd.persistence, demand.persistence])
    bottleneck_engine = 0.45 * margin.score + 0.35 * demand.acceleration + 0.20 * capital.score
    technology_engine = 0.65 * rd.score + 0.35 * margin.score
    boom_score = (
        0.27 * capital_engine
        + 0.27 * demand.score
        + 0.15 * breadth_engine
        + 0.13 * bottleneck_engine
        + 0.10 * technology_engine
        + 0.08 * persistence_engine
    )
    coverage = usable_companies / max(1, requested_companies)
    metric_coverage = _mean([capital.coverage, rd.coverage, demand.coverage, margin.coverage], 0.0)
    confidence = clamp(100.0 * (0.65 * coverage + 0.35 * metric_coverage))
    stage = stage_for(boom_score, confidence, persistence_engine, breadth_engine)

    reasons = [
        (capital_engine, f"기업 CAPEX·R&D 자금투입 점수 {capital_engine:.1f}"),
        (demand.score, f"매출 수요 가속 점수 {demand.score:.1f}"),
        (breadth_engine, f"참여기업 확산도 {breadth_engine:.1f}"),
        (bottleneck_engine, f"마진·수요·증설 기반 병목 점수 {bottleneck_engine:.1f}"),
        (persistence_engine, f"긍정 흐름 지속성 {persistence_engine:.1f}"),
    ]
    top_reasons = [text for _, text in sorted(reasons, reverse=True)[:3]]
    warnings: list[str] = []
    for signal in signals.values():
        warnings.extend(signal.warnings)
    if confidence < 60:
        warnings.append("데이터 신뢰도가 낮아 투자판정이 아니라 관찰용으로만 사용해야 합니다.")

    return ThemeResult(
        theme_id=theme_id,
        theme_name=theme_name,
        as_of=as_of,
        stage=stage,
        boom_score=round(boom_score, 2),
        boom_probability_6m=probability(boom_score, 6, confidence),
        boom_probability_12m=probability(boom_score, 12, confidence),
        boom_probability_24m=probability(boom_score, 24, confidence),
        data_confidence=round(confidence, 2),
        engines=signals,
        top_reasons=top_reasons,
        invalidations=invalidations,
        coverage={
            "requested_companies": requested_companies,
            "usable_companies": usable_companies,
            "company_coverage": round(coverage, 4),
            "metric_coverage": round(metric_coverage, 4),
        },
        warnings=sorted(set(warnings)),
    )
