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


def build_dart_theme_result(
    *,
    theme_id: str,
    theme_name: str,
    as_of: str,
    signals: dict[str, Signal],
    requested_companies: int,
    usable_companies: int,
    invalidations: list[str],
) -> ThemeResult:
    capital_count = signals["capital_events"]
    contract_count = signals["supply_contracts"]
    capital = signals.get("capital_amounts", capital_count)
    contracts = signals.get("contract_amounts", contract_count)
    cashflow_capex = signals.get("cashflow_capex")
    demand = signals["revenue"]
    margin = signals["operating_margin"]
    research = signals.get("research_momentum")

    breadth_parts = [capital.breadth, contracts.breadth, demand.breadth]
    persistence_parts = [capital.persistence, contracts.persistence, demand.persistence]
    if cashflow_capex:
        breadth_parts.append(cashflow_capex.breadth)
        persistence_parts.append(cashflow_capex.persistence)
    if research:
        breadth_parts.append(research.breadth)
        persistence_parts.append(research.persistence)
    breadth_engine = _mean(breadth_parts)
    persistence_engine = _mean(persistence_parts)

    research_score = research.score if research else 50.0
    cashflow_capex_score = cashflow_capex.score if cashflow_capex else 50.0
    boom_score = (
        0.13 * capital.score
        + 0.17 * contracts.score
        + 0.16 * cashflow_capex_score
        + 0.20 * demand.score
        + 0.16 * research_score
        + 0.06 * margin.score
        + 0.07 * breadth_engine
        + 0.05 * persistence_engine
    )
    coverage = usable_companies / max(1, requested_companies)
    metric_coverage = _mean(
        [capital.coverage, contracts.coverage, cashflow_capex.coverage if cashflow_capex else 0.0, demand.coverage, margin.coverage],
        0.0,
    )
    amount_coverage = _mean(
        [
            float(capital.raw.get("amount_coverage", 0.0)),
            float(contracts.raw.get("amount_coverage", 0.0)),
        ],
        0.0,
    )
    independent_source = 1.0 if research and research.coverage > 0 else 0.0
    raw_coverage_confidence = clamp(
        100.0
        * (
            0.42 * coverage
            + 0.28 * metric_coverage
            + 0.18 * amount_coverage
            + 0.12 * independent_source
        )
    )
    confidence_cap = 72.0 if independent_source else 58.0
    confidence = min(confidence_cap, raw_coverage_confidence)
    stage = stage_for(boom_score, confidence, persistence_engine, breadth_engine)
    reasons = [
        (capital.score, f"실제 투자금액·시설투자 가속 점수 {capital.score:.1f}"),
        (contracts.score, f"실제 공급계약·수주금액 가속 점수 {contracts.score:.1f}"),
        (cashflow_capex_score, f"현금흐름표 실집행 CAPEX 가속 점수 {cashflow_capex_score:.1f}"),
        (demand.score, f"매출 수요 가속 점수 {demand.score:.1f}"),
        (research_score, f"기술연구 확산 가속 점수 {research_score:.1f}"),
        (margin.score, f"영업이익률 개선 점수 {margin.score:.1f}"),
        (breadth_engine, f"참여기업·신호 확산도 {breadth_engine:.1f}"),
        (persistence_engine, f"긍정 흐름 지속성 {persistence_engine:.1f}"),
    ]
    warnings: list[str] = [
        "V0.3.0은 OpenDART 계약기간 배분·현금흐름표 CAPEX·실적과 arXiv 연구확산을 결합한 연구용 선행점수이며 아직 투자판정용이 아닙니다.",
        "붐 확률은 성공·실패 산업 워크포워드 백테스트로 보정되기 전의 순위 비교용 값입니다.",
    ]
    if amount_coverage < 0.5:
        warnings.append("시설투자·계약 공시의 금액 추출률이 50% 미만이라 공시 건수 신호를 함께 사용했습니다.")
    if not independent_source:
        warnings.append("arXiv 기술연구 확산 데이터가 없어 독립 데이터원 교차검증이 부족합니다.")
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
        top_reasons=[text for _, text in sorted(reasons, reverse=True)[:4]],
        invalidations=invalidations,
        coverage={
            "requested_companies": requested_companies,
            "usable_companies": usable_companies,
            "company_coverage": round(coverage, 4),
            "metric_coverage": round(metric_coverage, 4),
            "amount_coverage": round(amount_coverage, 4),
            "independent_research_source": bool(independent_source),
            "primary_sources": ["OpenDART", "arXiv"] if independent_source else ["OpenDART"],
        },
        warnings=sorted(set(warnings)),
    )


def build_event_signal(name: str, company_series: dict[str, list[tuple[str, float]]]) -> Signal:
    max_len = max((len(series) for series in company_series.values()), default=0)
    totals = [0.0] * max_len
    for series in company_series.values():
        values = [value for _, value in series]
        for index, value in enumerate(values[-max_len:]):
            totals[index] += value
    recent = sum(totals[-2:]) if totals else 0.0
    prior = sum(totals[-4:-2]) if len(totals) >= 4 else 0.0
    older = sum(totals[-6:-4]) if len(totals) >= 6 else 0.0
    baseline = sum(totals[:-2]) / max(1, len(totals[:-2])) if len(totals) > 2 else 0.0
    level_ratio = (recent / 2.0 - baseline) / (baseline + 1.0)
    velocity = (recent - prior) / (prior + 1.0)
    prior_velocity = (prior - older) / (older + 1.0)
    acceleration = velocity - prior_velocity
    recent_periods = totals[-4:]
    persistence = sum(1 for value in recent_periods if value > 0) / max(1, len(recent_periods))
    active = 0
    for series in company_series.values():
        if sum(value for _, value in series[-2:]) > 0:
            active += 1
    breadth = active / max(1, len(company_series))
    level_score = _feature_to_score(level_ratio, 28.0)
    velocity_score = _feature_to_score(velocity, 24.0)
    acceleration_score = _feature_to_score(acceleration, 20.0)
    persistence_score = 100.0 * persistence
    breadth_score = 100.0 * breadth
    score = (
        0.25 * level_score
        + 0.25 * velocity_score
        + 0.20 * acceleration_score
        + 0.15 * persistence_score
        + 0.15 * breadth_score
    )
    return Signal(
        name=name,
        score=round(score, 2),
        level=round(level_score, 2),
        velocity=round(velocity_score, 2),
        acceleration=round(acceleration_score, 2),
        persistence=round(persistence_score, 2),
        breadth=round(breadth_score, 2),
        coverage=round(len(company_series) / max(1, len(company_series)), 4),
        raw={
            "period_totals": totals,
            "recent_two_periods": recent,
            "prior_two_periods": prior,
            "active_company_count": active,
            "company_count": len(company_series),
        },
        warnings=[] if recent > 0 else ["최근 6개월 관련 공시가 없어 신호가 약합니다."],
    )


def build_amount_event_signal(
    name: str,
    amount_ratio_series: dict[str, list[tuple[str, float]]],
    count_signal: Signal,
    amount_coverage: float,
) -> Signal:
    """Blend normalized disclosed amounts with event counts.

    Amounts are transformed with log1p after scaling by 100 (percent of TTM
    revenue). Extraction coverage determines how strongly the amount signal
    replaces the count fallback.
    """
    transformed: dict[str, list[tuple[str, float]]] = {}
    for code, series in amount_ratio_series.items():
        transformed[code] = [
            (date, math.log1p(max(0.0, value) * 100.0)) for date, value in series
        ]
    amount_signal = build_event_signal(name + "_amount_component", transformed)
    coverage_weight = clamp(amount_coverage * 100.0, 0.0, 85.0) / 100.0
    blended_score = coverage_weight * amount_signal.score + (1.0 - coverage_weight) * count_signal.score
    warnings = list(amount_signal.warnings)
    if amount_coverage < 0.5:
        warnings.append("원문 금액 추출률이 낮아 공시 건수 신호를 보조적으로 사용했습니다.")
    return Signal(
        name=name,
        score=round(blended_score, 2),
        level=round(coverage_weight * amount_signal.level + (1 - coverage_weight) * count_signal.level, 2),
        velocity=round(coverage_weight * amount_signal.velocity + (1 - coverage_weight) * count_signal.velocity, 2),
        acceleration=round(
            coverage_weight * amount_signal.acceleration + (1 - coverage_weight) * count_signal.acceleration, 2
        ),
        persistence=round(
            coverage_weight * amount_signal.persistence + (1 - coverage_weight) * count_signal.persistence, 2
        ),
        breadth=round(coverage_weight * amount_signal.breadth + (1 - coverage_weight) * count_signal.breadth, 2),
        coverage=round(max(amount_signal.coverage * amount_coverage, count_signal.coverage * 0.5), 4),
        raw={
            "amount_coverage": round(amount_coverage, 4),
            "amount_component": amount_signal.to_dict(),
            "count_fallback": count_signal.to_dict(),
            "amount_weight": round(coverage_weight, 4),
        },
        warnings=sorted(set(warnings)),
    )


def build_research_signal(name: str, momentum: dict[str, Any] | None) -> Signal:
    if not momentum:
        return Signal(
            name=name,
            score=50.0,
            coverage=0.0,
            raw={},
            warnings=["기술연구 확산 데이터를 확보하지 못했습니다."],
        )
    counts = momentum.get("counts", {})
    try:
        recent = float(counts.get("recent", 0))
        prior = float(counts.get("prior", 0))
        older = float(counts.get("older", 0))
    except (TypeError, ValueError):
        recent = prior = older = 0.0
    velocity = (recent - prior) / (prior + 10.0)
    prior_velocity = (prior - older) / (older + 10.0)
    acceleration = velocity - prior_velocity
    level = math.log1p(recent) - math.log1p(max(1.0, (prior + older) / 2.0))
    persistence = (float(recent > prior) + float(prior > older)) / 2.0
    level_score = _feature_to_score(level, 24.0)
    velocity_score = _feature_to_score(velocity, 32.0)
    acceleration_score = _feature_to_score(acceleration, 28.0)
    persistence_score = 100.0 * persistence
    score = (
        0.20 * level_score
        + 0.35 * velocity_score
        + 0.30 * acceleration_score
        + 0.15 * persistence_score
    )
    return Signal(
        name=name,
        score=round(score, 2),
        level=round(level_score, 2),
        velocity=round(velocity_score, 2),
        acceleration=round(acceleration_score, 2),
        persistence=round(persistence_score, 2),
        breadth=round(persistence_score, 2),
        coverage=1.0,
        raw=momentum,
        warnings=[] if recent > 0 else ["최근 12개월 관련 arXiv 논문이 검색되지 않았습니다."],
    )


def build_annual_capex_signal(
    name: str,
    capex_ratio_series: dict[str, list[tuple[str, float]]],
) -> Signal:
    """Score annual cash-flow CAPEX intensity and acceleration across companies."""
    company_scores: list[float] = []
    latest_growths: list[float] = []
    accelerations: list[float] = []
    positive = 0
    persistent = 0
    usable = 0
    raw: dict[str, Any] = {}
    for code, series in capex_ratio_series.items():
        values = [max(0.0, float(value)) for _, value in sorted(series)]
        if len(values) < 3:
            continue
        usable += 1
        latest = values[-1]
        prior = values[-2]
        older = values[-3]
        growth = (latest - prior) / (abs(prior) + 0.01)
        prior_growth = (prior - older) / (abs(older) + 0.01)
        accel = growth - prior_growth
        level_score = _feature_to_score(math.log1p(latest * 20.0), 18.0)
        growth_score = _feature_to_score(growth, 22.0)
        accel_score = _feature_to_score(accel, 18.0)
        trend_persistence = 100.0 * ((float(latest > prior) + float(prior > older)) / 2.0)
        company_score = 0.28 * level_score + 0.32 * growth_score + 0.25 * accel_score + 0.15 * trend_persistence
        company_scores.append(company_score)
        latest_growths.append(growth)
        accelerations.append(accel)
        positive += int(growth > 0)
        persistent += int(latest > prior > older)
        raw[code] = {
            "latest_capex_to_revenue": latest,
            "prior_capex_to_revenue": prior,
            "older_capex_to_revenue": older,
            "growth": growth,
            "acceleration": accel,
        }
    breadth = positive / usable if usable else 0.0
    persistence = persistent / usable if usable else 0.0
    score = 0.75 * _mean(company_scores) + 0.15 * 100.0 * breadth + 0.10 * 100.0 * persistence if usable else 50.0
    return Signal(
        name=name,
        score=round(score, 2),
        level=round(_mean([50.0 + min(50.0, max(-50.0, value * 100.0)) for value in latest_growths]), 2),
        velocity=round(_feature_to_score(_mean(latest_growths, 0.0), 22.0), 2),
        acceleration=round(_feature_to_score(_mean(accelerations, 0.0), 18.0), 2),
        persistence=round(100.0 * persistence, 2),
        breadth=round(100.0 * breadth, 2),
        coverage=round(usable / max(1, len(capex_ratio_series)), 4),
        raw=raw,
        warnings=[] if usable else ["연간 현금흐름표 CAPEX 데이터가 부족합니다."],
    )
