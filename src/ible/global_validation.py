from __future__ import annotations

import datetime as dt
import json
import os
import statistics
from pathlib import Path
from typing import Any

from ible.analytics.exposure_scoring import (
    build_exposure_weighted_margin_signal,
    build_exposure_weighted_signal,
    effective_weight,
    harmonic_mean,
)
from ible.analytics.scoring import build_research_signal, clamp, probability
from ible.analytics.sec_metrics import FLOW_TAGS, quarterly_flow
from ible.backtest import evaluate_scenario
from ible.collectors.arxiv import ArxivClient
from ible.collectors.fmp import FmpClient
from ible.collectors.sec_bulk import SecBulkClient
from ible.config import load_yaml
from ible.http import JsonHttpClient

ALERT_STAGES = {"EARLY_ACCUMULATION", "CAPITAL_LED_ACCUMULATION", "TRANSITION", "COMMERCIAL_BOOM"}
METRICS = ("capex", "rd", "revenue", "gross_profit", "operating_income")


def _stage(score: float, confidence: float, cross: float, commercial: float) -> str:
    if confidence < 45:
        return "INSUFFICIENT_DATA"
    if score >= 64 and cross >= 54 and commercial < 60:
        return "EARLY_ACCUMULATION"
    if score >= 58 and cross >= 52:
        return "CAPITAL_LED_ACCUMULATION"
    if score >= 58 and commercial >= 58:
        return "TRANSITION"
    if commercial >= 68:
        return "COMMERCIAL_BOOM"
    if score >= 53:
        return "WATCH"
    return "NO_SIGNAL"


def _theme_result(
    theme: dict[str, Any],
    all_series: dict[str, dict[str, list[tuple[str, float]]]],
    research_momentum: dict[str, Any] | None,
    as_of: str,
    minimum_exposure: float,
) -> dict[str, Any]:
    profiles = {str(row["ticker"]).upper(): row for row in theme.get("us_companies", [])}
    series: dict[str, dict[str, list[tuple[str, float]]]] = {
        metric: {ticker: all_series.get(metric, {}).get(ticker, []) for ticker in profiles}
        for metric in METRICS
    }

    capex = build_exposure_weighted_signal("us_exposure_capex", series["capex"], profiles, minimum_exposure)
    rd = build_exposure_weighted_signal("us_exposure_rd", series["rd"], profiles, minimum_exposure)
    revenue = build_exposure_weighted_signal("us_exposure_revenue", series["revenue"], profiles, minimum_exposure)
    margin = build_exposure_weighted_margin_signal(series["revenue"], series["gross_profit"], profiles, minimum_exposure)
    research = build_research_signal("technology_research_diffusion", research_momentum)

    innovation = 0.55 * rd.score + 0.45 * research.score
    cross = harmonic_mean([capex.score, revenue.score, innovation])
    weighted_breadth = statistics.mean([capex.breadth, rd.breadth, revenue.breadth])
    commercial = 0.45 * revenue.score + 0.25 * margin.score + 0.30 * capex.score
    raw_early = (
        0.32 * capex.score
        + 0.23 * rd.score
        + 0.18 * research.score
        + 0.17 * revenue.score
        + 0.10 * weighted_breadth
    )
    hype_gap = max(0.0, research.score - statistics.mean([capex.score, revenue.score]))
    hype_penalty = max(0.0, hype_gap - 12.0) * 0.32
    exposure_coverage = statistics.mean([capex.coverage, rd.coverage, revenue.coverage])
    eligible_weights = [
        effective_weight(p)
        for p in profiles.values()
        if float(p.get("exposure", 0)) >= minimum_exposure
    ]
    exposure_quality = (
        100.0 * min(1.0, sum(eligible_weights) / max(1.5, len(eligible_weights) * 0.65))
        if eligible_weights
        else 0.0
    )
    coverage_penalty = max(0.0, 0.60 - exposure_coverage) * 30.0
    boom_score = clamp(
        0.62 * raw_early
        + 0.23 * cross
        + 0.15 * commercial
        - hype_penalty
        - coverage_penalty
    )
    confidence = clamp(
        100.0
        * (
            0.55 * exposure_coverage
            + 0.25 * min(1.0, len(eligible_weights) / 4.0)
            + 0.20 * exposure_quality / 100.0
        )
    )
    stage = _stage(boom_score, confidence, cross, commercial)
    usable = sum(
        1
        for ticker in profiles
        if len(series["revenue"].get(ticker, [])) >= 5
        or len(series["capex"].get(ticker, [])) >= 5
    )
    early = clamp(raw_early - hype_penalty - coverage_penalty)

    return {
        "theme_id": theme["id"],
        "theme_name": theme["name"],
        "as_of": as_of,
        "stage": stage,
        "boom_score": round(boom_score, 2),
        "early_signal_score": round(early, 2),
        "commercial_realization_score": round(commercial, 2),
        "cross_confirmation_score": round(cross, 2),
        "transition_gap_score": round(clamp(50 + 1.2 * (early - commercial)), 2),
        "prediction_score_6m": round(0.4 * early + 0.6 * commercial, 2),
        "prediction_score_12m": round(0.7 * early + 0.3 * commercial, 2),
        "prediction_score_24m": round(0.82 * early + 0.18 * cross, 2),
        "boom_probability_6m": probability(0.4 * early + 0.6 * commercial, 6, confidence),
        "boom_probability_12m": probability(0.7 * early + 0.3 * commercial, 12, confidence),
        "boom_probability_24m": probability(0.82 * early + 0.18 * cross, 24, confidence),
        "data_confidence": round(confidence, 2),
        "engines": {
            "capex": capex.to_dict(),
            "rd": rd.to_dict(),
            "revenue": revenue.to_dict(),
            "margin": margin.to_dict(),
            "research": research.to_dict(),
        },
        "top_reasons": [
            f"노출도 통과 기업 CAPEX {capex.score:.1f}",
            f"노출도 통과 기업 매출수요 {revenue.score:.1f}",
            f"기술·기업 R&D 결합 {innovation:.1f}",
            f"독립신호 교차확인 {cross:.1f}",
        ],
        "invalidations": theme.get("invalidations", []),
        "coverage": {
            "requested_companies": len(profiles),
            "eligible_companies": len(eligible_weights),
            "usable_companies": usable,
            "company_coverage": round(usable / max(1, len(profiles)), 4),
            "metric_coverage": round(exposure_coverage, 4),
            "exposure_quality": round(exposure_quality, 2),
        },
        "diagnostics": {
            "hype_gap": round(hype_gap, 2),
            "hype_penalty": round(hype_penalty, 2),
            "coverage_penalty": round(coverage_penalty, 2),
            "evaluation_class": theme.get("evaluation_class", "structural"),
        },
        "warnings": [w for signal in (capex, rd, revenue, margin, research) for w in signal.warnings],
    }


def _load_sec_series(
    root: Path,
    tickers: list[str],
    as_of: str,
) -> tuple[dict[str, dict[str, list[tuple[str, float]]]], dict[str, Any], dict[str, str]]:
    cik_map = json.loads((root / "config" / "sec_cik_map.json").read_text(encoding="utf-8"))
    client = SecBulkClient(root / ".cache" / "sec_bulk", os.getenv("SEC_USER_AGENT", ""))
    status = client.prepare_subset(cik_map, tickers)
    facts, errors = client.load_subset(tickers)
    series: dict[str, dict[str, list[tuple[str, float]]]] = {metric: {} for metric in METRICS}
    for ticker in tickers:
        payload = facts.get(ticker)
        for metric in METRICS:
            values: list[tuple[str, float]] = []
            if payload:
                _, values = quarterly_flow(payload, FLOW_TAGS[metric], as_of)
            series[metric][ticker] = values
    return series, status, errors


def _load_fmp_series(
    root: Path,
    tickers: list[str],
    as_of: str,
) -> tuple[dict[str, dict[str, list[tuple[str, float]]]], dict[str, Any], dict[str, str]]:
    client = FmpClient(root / ".cache" / "fmp", os.getenv("FMP_API_KEY", ""))
    status = client.prepare_subset(tickers)
    series, errors = client.load_series(tickers, as_of)
    return series, status, errors


def run_global_holdout(root: Path, output_dir: Path) -> dict[str, Any]:
    config = load_yaml(root / "config" / "global_holdouts.yml")
    exposure_config = load_yaml(root / "config" / "theme_exposures.yml")
    minimum_exposure = float(exposure_config.get("minimum_exposure", 0.30))
    themes_by_id = {row["id"]: row for row in exposure_config.get("themes", [])}
    cohort_ids = list(config["cohort"]["theme_ids"])
    themes = [themes_by_id[theme_id] for theme_id in cohort_ids]
    as_of = str(config["cohort"]["as_of"])
    tickers = sorted(
        {
            company["ticker"]
            for theme in themes
            for company in theme.get("us_companies", [])
            if float(company.get("exposure", 0)) >= minimum_exposure
        }
    )

    financial_source = os.getenv("FINANCIAL_SOURCE", "fmp").strip().lower()
    if financial_source == "fmp":
        all_series, financial_status, financial_errors = _load_fmp_series(root, tickers, as_of)
    elif financial_source == "sec":
        all_series, financial_status, financial_errors = _load_sec_series(root, tickers, as_of)
    else:
        raise ValueError("FINANCIAL_SOURCE must be 'fmp' or 'sec'")

    http = JsonHttpClient(
        user_agent=os.getenv("SEC_USER_AGENT", "IndustryBoomLeadingEngine/0.8.4"),
        timeout=20,
        min_interval=3.2,
        retries=1,
        cache_dir=root / ".cache" / "http",
    )
    arxiv = ArxivClient(http)
    research: dict[str, dict[str, Any]] = {}
    research_errors: dict[str, str] = {}
    cutoff = dt.date.fromisoformat(as_of)
    for index, theme in enumerate(themes, 1):
        try:
            research[theme["id"]] = arxiv.momentum(theme["arxiv_query"], cutoff)
        except Exception as exc:  # noqa: BLE001
            research_errors[theme["id"]] = str(exc)
        print(f"[GLOBAL] arXiv {index}/{len(themes)} errors={len(research_errors)}", flush=True)

    ranking = [
        _theme_result(theme, all_series, research.get(theme["id"]), as_of, minimum_exposure)
        for theme in themes
    ]
    ranking.sort(key=lambda row: (row["boom_score"], row["data_confidence"]), reverse=True)
    scenarios = []
    for raw in config.get("scenarios", []):
        scenario = dict(raw)
        scenario["comparison_theme_ids"] = cohort_ids
        scenarios.append(evaluate_scenario(scenario, ranking))

    eligible = [scenario for scenario in scenarios if scenario.get("status") != "INSUFFICIENT_DATA"]
    positives = [scenario for scenario in eligible if scenario.get("label") == "positive"]
    negatives = [scenario for scenario in eligible if scenario.get("label") == "negative"]
    recall = sum(bool(s.get("passed")) for s in positives) / len(positives) if positives else None
    false_alarm = (
        sum(bool(s.get("alert_triggered")) for s in negatives) / len(negatives)
        if negatives
        else None
    )
    pairwise = []
    for positive in positives:
        for negative in negatives:
            positive_score = float((positive.get("observed") or {}).get("boom_score") or 0)
            negative_score = float((negative.get("observed") or {}).get("boom_score") or 0)
            pairwise.append(
                1.0 if positive_score > negative_score else 0.5 if positive_score == negative_score else 0.0
            )
    auc = statistics.mean(pairwise) if pairwise else None
    passed = bool(
        recall is not None
        and recall >= 0.75
        and false_alarm is not None
        and false_alarm <= 0.25
        and auc is not None
        and auc >= 0.70
    )

    summary = {
        "status": "PASSED_V084_GLOBAL_HOLDOUT" if passed else "FAILED_V084_GLOBAL_HOLDOUT",
        "investment_use_allowed": False,
        "financial_source": financial_source,
        "metrics": {
            "eligible_scenarios": len(eligible),
            "positive_recall": round(recall, 4) if recall is not None else None,
            "false_alarm_rate": round(false_alarm, 4) if false_alarm is not None else None,
            "pairwise_auc": round(auc, 4) if auc is not None else None,
        },
        "criteria": {
            "positive_recall_min": 0.75,
            "false_alarm_rate_max": 0.25,
            "pairwise_auc_min": 0.70,
        },
        "financial_data": financial_status,
        "financial_data_errors": financial_errors,
        "research_errors": research_errors,
        "ranking": ranking,
        "scenarios": scenarios,
        "known_limitations": [
            "테마 노출도는 보수적 수동 프로필이며 사업부 매출 공시로 계속 갱신해야 합니다.",
            "FMP 표준화 재무는 SEC 원공시를 재가공한 제3자 데이터이므로 출처 교차검증이 필요합니다.",
            "시장 미반영도와 실제 주가수익 백테스트는 아직 포함되지 않았습니다.",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in {
        "global_holdout_summary.json": summary,
        "global_holdout_ranking.json": ranking,
        "global_holdout_scenarios.json": scenarios,
        "financial_data_status.json": financial_status,
    }.items():
        (output_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return summary
