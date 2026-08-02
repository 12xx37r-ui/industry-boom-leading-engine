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


def _harmonic_mean(values: list[float], default: float = 50.0) -> float:
    clean = [max(1e-6, float(value)) for value in values if math.isfinite(value)]
    if not clean:
        return default
    return len(clean) / sum(1.0 / value for value in clean)


def phase_for(
    early_signal: float,
    commercial_realization: float,
    preboom_score: float,
    confidence: float,
    cross_confirmation: float,
) -> str:
    """Classify where the industry sits in the capital-to-boom sequence."""
    if confidence < 50:
        return "INSUFFICIENT_DATA"
    if early_signal >= 60 and commercial_realization < 55 and cross_confirmation >= 52:
        return "EARLY_ACCUMULATION"
    if early_signal >= 56 and commercial_realization < 57 and cross_confirmation >= 54:
        return "CAPITAL_LED_ACCUMULATION"
    if early_signal >= 58 and commercial_realization >= 55:
        return "TRANSITION"
    if commercial_realization >= 64:
        return "COMMERCIAL_BOOM"
    if preboom_score >= 55:
        return "WATCH"
    return "NO_SIGNAL"


def _phase_scores(
    *,
    capital: Signal,
    contracts: Signal,
    cashflow_capex: Signal,
    demand: Signal,
    margin: Signal,
    research: Signal,
    breadth_engine: float,
) -> dict[str, float]:
    """Separate early capital formation from already-realized commercial strength.

    The old single score overweighted contracts and current revenue. That is useful
    for detecting an industry already in a boom, but it can miss the user's target:
    research acceleration -> real CAPEX -> early demand before broad commercialization.
    """
    capital_formation = 0.65 * cashflow_capex.score + 0.35 * capital.score
    research_led_early = (
        0.35 * research.score
        + 0.30 * capital_formation
        + 0.20 * demand.score
        + 0.05 * margin.score
        + 0.10 * breadth_engine
    )
    capital_led_early = (
        0.15 * research.score
        + 0.45 * capital_formation
        + 0.25 * demand.score
        + 0.05 * margin.score
        + 0.10 * breadth_engine
    )
    # Structural booms do not all begin the same way. Software/AI themes can be
    # research-led, while EV/battery, grid and industrial themes can be led by
    # hard CAPEX before the research count accelerates. Use the stronger pathway,
    # but still require independent cross-confirmation below.
    early_signal = max(research_led_early, capital_led_early)
    commercial_realization = (
        0.25 * capital.score
        + 0.30 * contracts.score
        + 0.30 * demand.score
        + 0.15 * margin.score
    )
    cross_confirmation = _harmonic_mean(
        [research.score, capital_formation, demand.score, breadth_engine]
    )
    transition_gap = clamp(50.0 + 1.5 * (early_signal - commercial_realization))
    preboom_score = (
        0.55 * early_signal
        + 0.25 * cross_confirmation
        + 0.20 * transition_gap
    )
    prediction_6m = 0.35 * preboom_score + 0.65 * commercial_realization
    prediction_12m = 0.70 * preboom_score + 0.30 * commercial_realization
    prediction_24m = 0.82 * early_signal + 0.18 * cross_confirmation
    dominant_path = "RESEARCH_LED" if research_led_early >= capital_led_early else "CAPITAL_LED"
    return {
        "capital_formation": clamp(capital_formation),
        "research_led_early": clamp(research_led_early),
        "capital_led_early": clamp(capital_led_early),
        "dominant_path": dominant_path,
        "early_signal": clamp(early_signal),
        "commercial_realization": clamp(commercial_realization),
        "cross_confirmation": clamp(cross_confirmation),
        "transition_gap": clamp(transition_gap),
        "preboom_score": clamp(preboom_score),
        "prediction_6m": clamp(prediction_6m),
        "prediction_12m": clamp(prediction_12m),
        "prediction_24m": clamp(prediction_24m),
    }


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
    breadth_engine = _mean([capital.breadth, rd.breadth, demand.breadth])
    persistence_engine = _mean([capital.persistence, rd.persistence, demand.persistence])
    phase = _phase_scores(
        capital=capital,
        contracts=demand,
        cashflow_capex=capital,
        demand=demand,
        margin=margin,
        research=rd,
        breadth_engine=breadth_engine,
    )
    coverage = usable_companies / max(1, requested_companies)
    metric_coverage = _mean([capital.coverage, rd.coverage, demand.coverage, margin.coverage], 0.0)
    confidence = clamp(100.0 * (0.65 * coverage + 0.35 * metric_coverage))
    stage = phase_for(
        phase["early_signal"],
        phase["commercial_realization"],
        phase["preboom_score"],
        confidence,
        phase["cross_confirmation"],
    )

    reasons = [
        (phase["early_signal"], f"초기 자금·기술 선행점수 {phase['early_signal']:.1f}"),
        (phase["commercial_realization"], f"상업화 실현점수 {phase['commercial_realization']:.1f}"),
        (phase["cross_confirmation"], f"독립 신호 교차확인 {phase['cross_confirmation']:.1f}"),
        (breadth_engine, f"참여기업 확산도 {breadth_engine:.1f}"),
        (persistence_engine, f"긍정 흐름 지속성 {persistence_engine:.1f}"),
    ]
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
        boom_score=round(phase["preboom_score"], 2),
        boom_probability_6m=probability(phase["prediction_6m"], 6, confidence),
        boom_probability_12m=probability(phase["prediction_12m"], 12, confidence),
        boom_probability_24m=probability(phase["prediction_24m"], 24, confidence),
        data_confidence=round(confidence, 2),
        early_signal_score=round(phase["early_signal"], 2),
        commercial_realization_score=round(phase["commercial_realization"], 2),
        cross_confirmation_score=round(phase["cross_confirmation"], 2),
        transition_gap_score=round(phase["transition_gap"], 2),
        prediction_score_6m=round(phase["prediction_6m"], 2),
        prediction_score_12m=round(phase["prediction_12m"], 2),
        prediction_score_24m=round(phase["prediction_24m"], 2),
        engines=signals,
        top_reasons=[text for _, text in sorted(reasons, reverse=True)[:4]],
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
    cashflow_capex = signals.get("cashflow_capex") or capital
    demand = signals["revenue"]
    margin = signals["operating_margin"]
    research = signals.get("research_momentum") or Signal(
        name="technology_research_diffusion",
        score=50.0,
        coverage=0.0,
        warnings=["기술연구 확산 데이터가 없습니다."],
    )

    breadth_parts = [
        capital.breadth,
        contracts.breadth,
        cashflow_capex.breadth,
        demand.breadth,
        research.breadth,
    ]
    persistence_parts = [
        capital.persistence,
        contracts.persistence,
        cashflow_capex.persistence,
        demand.persistence,
        research.persistence,
    ]
    breadth_engine = _mean(breadth_parts)
    persistence_engine = _mean(persistence_parts)
    phase = _phase_scores(
        capital=capital,
        contracts=contracts,
        cashflow_capex=cashflow_capex,
        demand=demand,
        margin=margin,
        research=research,
        breadth_engine=breadth_engine,
    )

    coverage = usable_companies / max(1, requested_companies)
    metric_coverage = _mean(
        [
            capital.coverage,
            contracts.coverage,
            cashflow_capex.coverage,
            demand.coverage,
            margin.coverage,
            research.coverage,
        ],
        0.0,
    )
    amount_coverage = _mean(
        [
            float(capital.raw.get("amount_coverage", 0.0)),
            float(contracts.raw.get("amount_coverage", 0.0)),
        ],
        0.0,
    )
    independent_source = 1.0 if research.coverage > 0 else 0.0
    raw_coverage_confidence = clamp(
        100.0
        * (
            0.40 * coverage
            + 0.28 * metric_coverage
            + 0.18 * amount_coverage
            + 0.14 * independent_source
        )
    )
    confidence_cap = 74.0 if independent_source else 58.0
    confidence = min(confidence_cap, raw_coverage_confidence)
    stage = phase_for(
        phase["early_signal"],
        phase["commercial_realization"],
        phase["preboom_score"],
        confidence,
        phase["cross_confirmation"],
    )

    pathway_label = "연구주도" if phase["dominant_path"] == "RESEARCH_LED" else "실물투자주도"
    reasons = [
        (phase["early_signal"], f"{pathway_label} 선행점수 {phase['early_signal']:.1f}"),
        (phase["cross_confirmation"], f"연구·CAPEX·매출 교차확인 {phase['cross_confirmation']:.1f}"),
        (phase["transition_gap"], f"실적 대중화 전 선행격차 {phase['transition_gap']:.1f}"),
        (phase["commercial_realization"], f"상업화 실현점수 {phase['commercial_realization']:.1f}"),
        (cashflow_capex.score, f"현금흐름표 실집행 CAPEX {cashflow_capex.score:.1f}"),
        (research.score, f"기술연구 확산 가속 {research.score:.1f}"),
        (demand.score, f"매출 수요 가속 {demand.score:.1f}"),
        (breadth_engine, f"참여기업·신호 확산도 {breadth_engine:.1f}"),
        (persistence_engine, f"긍정 흐름 지속성 {persistence_engine:.1f}"),
    ]
    warnings: list[str] = [
        "V0.6.0은 연구주도·실물투자주도 두 선행경로와 다중 성공·실패 산업 검증을 분리해 계산합니다.",
        "붐 확률은 성공·실패 산업 전체 워크포워드 백테스트 전의 상대비교용 값입니다.",
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
        boom_score=round(phase["preboom_score"], 2),
        boom_probability_6m=probability(phase["prediction_6m"], 6, confidence),
        boom_probability_12m=probability(phase["prediction_12m"], 12, confidence),
        boom_probability_24m=probability(phase["prediction_24m"], 24, confidence),
        data_confidence=round(confidence, 2),
        early_signal_score=round(phase["early_signal"], 2),
        commercial_realization_score=round(phase["commercial_realization"], 2),
        cross_confirmation_score=round(phase["cross_confirmation"], 2),
        transition_gap_score=round(phase["transition_gap"], 2),
        prediction_score_6m=round(phase["prediction_6m"], 2),
        prediction_score_12m=round(phase["prediction_12m"], 2),
        prediction_score_24m=round(phase["prediction_24m"], 2),
        engines=signals,
        top_reasons=[reason for _, reason in sorted(reasons, reverse=True)[:5]],
        invalidations=invalidations,
        coverage={
            "requested_companies": requested_companies,
            "usable_companies": usable_companies,
            "company_coverage": round(coverage, 4),
            "metric_coverage": round(metric_coverage, 4),
            "amount_coverage": round(amount_coverage, 4),
            "independent_research_source": bool(independent_source),
            "primary_sources": ["OpenDART", "arXiv"] if independent_source else ["OpenDART"],
            "score_definition": "preboom_score",
            "leading_pathways": {
                "research_led_score": round(phase["research_led_early"], 2),
                "capital_led_score": round(phase["capital_led_early"], 2),
                "dominant_path": phase["dominant_path"],
            },
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
