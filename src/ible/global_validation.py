from __future__ import annotations

import json
import statistics
from copy import deepcopy
from pathlib import Path
from typing import Any

from ible.analytics.exposure_scoring import (
    build_exposure_weighted_margin_signal,
    build_exposure_weighted_signal,
    effective_weight,
    harmonic_mean,
)
from ible.analytics.scoring import build_research_signal, clamp, probability
from ible.backtest import evaluate_scenario
from ible.collectors.sec_fsds import METRICS, SecFsdsClient
from ible.config import load_yaml

ALERT_STAGES = {"EARLY_ACCUMULATION", "CAPITAL_LED_ACCUMULATION", "TRANSITION", "COMMERCIAL_BOOM"}


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
    historically_eligible: set[str],
) -> dict[str, Any]:
    original_profiles = {str(row["ticker"]).upper(): row for row in theme.get("us_companies", [])}
    profiles = {
        ticker: profile
        for ticker, profile in original_profiles.items()
        if ticker in historically_eligible
    }
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
        effective_weight(profile)
        for profile in profiles.values()
        if float(profile.get("exposure", 0)) >= minimum_exposure
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
            "configured_companies": len(original_profiles),
            "historically_eligible_companies": len(profiles),
            "historically_ineligible_companies": sorted(set(original_profiles) - set(profiles)),
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
        "warnings": [warning for signal in (capex, rd, revenue, margin, research) for warning in signal.warnings],
    }


def _load_sec_fsds_series(
    root: Path,
    tickers: list[str],
) -> tuple[dict[str, dict[str, list[tuple[str, float]]]], dict[str, Any], dict[str, str]]:
    client = SecFsdsClient(root / ".cache" / "sec_fsds", "offline-seed@example.invalid")
    return client.load_seed(root / "validation_seed" / "sec_fsds_fy2021.json", tickers)


def _load_offline_research(root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    path = root / "validation_seed" / "sec_fsds_fy2021.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, {"_seed": f"offline research seed unavailable: {exc}"}
    research = {
        str(key): value
        for key, value in (payload.get("research") or {}).items()
        if isinstance(value, dict)
    }
    status = payload.get("status") or {}
    errors = {str(key): str(value) for key, value in (status.get("research_errors") or {}).items()}
    return research, errors


def _mark_insufficient(scenario: dict[str, Any], reason: str) -> dict[str, Any]:
    result = deepcopy(scenario)
    result["status"] = "INSUFFICIENT_DATA"
    result["passed"] = False
    result["alert_triggered"] = False
    result["insufficient_reason"] = reason
    return result


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
            str(company["ticker"]).upper()
            for theme in themes
            for company in theme.get("us_companies", [])
            if float(company.get("exposure", 0)) >= minimum_exposure
        }
    )

    all_series, financial_status, financial_errors = _load_sec_fsds_series(root, tickers)
    historically_eligible = set(financial_status.get("historically_eligible") or [])
    dataset_gate_passed = bool(
        financial_status.get("status") == "READY"
        and float(financial_status.get("coverage_of_historically_eligible") or 0) >= 0.75
        and int(financial_status.get("available") or 0) >= 20
        and len(financial_status.get("periods_downloaded") or []) == len(financial_status.get("periods_required") or [])
    )

    research, research_errors = _load_offline_research(root)
    required_research = {theme["id"] for theme in themes}
    missing_research = sorted(required_research - set(research))
    for theme_id in missing_research:
        research_errors.setdefault(theme_id, "offline arXiv research seed missing")
    research_gate_passed = len(required_research & set(research)) >= 6
    dataset_gate_passed = bool(dataset_gate_passed and research_gate_passed)
    if not dataset_gate_passed:
        print(
            f"[GLOBAL] offline seed gate failed status={financial_status.get('status')} "
            f"available={financial_status.get('available', 0)} "
            f"research={len(required_research & set(research))}/{len(required_research)}; "
            "producing insufficient-data output",
            flush=True,
        )
    else:
        print(
            f"[GLOBAL] offline seed ready financial={financial_status.get('available', 0)} "
            f"research={len(required_research & set(research))}/{len(required_research)}",
            flush=True,
        )

    ranking = [
        _theme_result(
            theme,
            all_series,
            research.get(theme["id"]),
            as_of,
            minimum_exposure,
            historically_eligible,
        )
        for theme in themes
    ]
    ranking.sort(key=lambda row: (row["boom_score"], row["data_confidence"]), reverse=True)

    scenarios: list[dict[str, Any]] = []
    for raw in config.get("scenarios", []):
        scenario = dict(raw)
        scenario["comparison_theme_ids"] = cohort_ids
        evaluated = evaluate_scenario(scenario, ranking)
        if not dataset_gate_passed:
            evaluated = _mark_insufficient(evaluated, "SEC FSDS coverage gate failed")
        scenarios.append(evaluated)

    eligible = [scenario for scenario in scenarios if scenario.get("status") != "INSUFFICIENT_DATA"]
    positives = [scenario for scenario in eligible if scenario.get("label") == "positive"]
    negatives = [scenario for scenario in eligible if scenario.get("label") == "negative"]
    recall = sum(bool(scenario.get("passed")) for scenario in positives) / len(positives) if positives else None
    false_alarm = (
        sum(bool(scenario.get("alert_triggered")) for scenario in negatives) / len(negatives)
        if negatives
        else None
    )
    pairwise: list[float] = []
    for positive in positives:
        for negative in negatives:
            positive_score = float((positive.get("observed") or {}).get("boom_score") or 0)
            negative_score = float((negative.get("observed") or {}).get("boom_score") or 0)
            pairwise.append(
                1.0 if positive_score > negative_score else 0.5 if positive_score == negative_score else 0.0
            )
    auc = statistics.mean(pairwise) if pairwise else None
    passed = bool(
        dataset_gate_passed
        and recall is not None
        and recall >= 0.75
        and false_alarm is not None
        and false_alarm <= 0.25
        and auc is not None
        and auc >= 0.70
    )
    if not dataset_gate_passed or not eligible:
        run_status = "INSUFFICIENT_V088_GLOBAL_HOLDOUT"
    else:
        run_status = "PASSED_V088_GLOBAL_HOLDOUT" if passed else "FAILED_V088_GLOBAL_HOLDOUT"

    summary = {
        "status": run_status,
        "investment_use_allowed": False,
        "financial_source": "offline_sec_fsds_plus_arxiv_seed",
        "dataset_gate_passed": dataset_gate_passed,
        "metrics": {
            "eligible_scenarios": len(eligible),
            "positive_recall": round(recall, 4) if recall is not None else None,
            "false_alarm_rate": round(false_alarm, 4) if false_alarm is not None else None,
            "pairwise_auc": round(auc, 4) if auc is not None else None,
        },
        "criteria": {
            "financial_coverage_min": 0.75,
            "available_company_min": 20,
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
            "SEC Financial Statement Data Sets의 원공시 숫자를 로컬에서 추출하지만 XBRL 태그 선택과 분기값 환산에는 모델링 판단이 포함됩니다.",
            "2021Q1~2022Q2 자료와 2022-04-30 이전 arXiv 시계열을 로컬 seed에 고정해 GitHub 실행 중 외부 API를 호출하지 않습니다.",
            "당시 상장·공시 이력이 없는 현재 기업은 역사적 분모에서 제외합니다.",
            "테마 노출도는 보수적 수동 프로필이며 사업부 매출 공시로 계속 갱신해야 합니다.",
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
        (output_dir / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
