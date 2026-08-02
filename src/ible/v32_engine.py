from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ible.integrity import canonical_sha256, load_json, write_json
from ible.model_lock import load_and_verify_model_lock
from ible.v3_http import HttpSettings, JsonHttpClient
from ible.v32_investment import (
    aggregate_capex,
    aggregate_rd,
    bounded_growth_score,
    load_official_workbook,
    parse_aies_capex,
    parse_berd_rd,
    percentile_scores,
    select_naics_rows,
)


class V32Error(RuntimeError):
    pass


def _load_phase2(root: Path) -> dict[str, dict[str, Any]]:
    path = root / "data_cache/latest/v31_real_economy_observations.json"
    if not path.is_file():
        return {}
    try:
        payload = load_json(path)
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        str(row.get("theme_id")): row
        for row in payload.get("themes") or []
        if row.get("theme_id")
    }


def _weighted_available(parts: list[tuple[float, float | None]]) -> float | None:
    observed = [(weight, float(value)) for weight, value in parts if value is not None]
    if not observed:
        return None
    total_weight = sum(weight for weight, _ in observed)
    if total_weight <= 0:
        return None
    return round(sum(weight * value for weight, value in observed) / total_weight, 4)


def run_v32(root: Path, output_dir: Path, run_date: str | None = None) -> dict[str, Any]:
    config = load_json(root / "config/v32_investment_sources.json")
    mapping = load_json(root / "config/v31_theme_naics.json")
    themes = list(mapping.get("themes") or [])
    if len(themes) != int(config["minimum_theme_count"]):
        raise V32Error("theme mapping count mismatch")

    model_lock = load_and_verify_model_lock(root)
    timezone = ZoneInfo(str(config.get("timezone") or "Asia/Seoul"))
    now = datetime.now(timezone)
    today = date.fromisoformat(run_date) if run_date else now.date()
    as_of = today - timedelta(days=1)
    captured_at = now.isoformat(timespec="seconds")

    network = config["network"]
    client = JsonHttpClient(HttpSettings(
        timeout_seconds=int(network["timeout_seconds"]),
        max_attempts=int(network["max_attempts"]),
        base_backoff_seconds=float(network["base_backoff_seconds"]),
        user_agent=str(network["user_agent"]),
    ))

    capex_source = config["sources"]["aies_capex"]
    rd_source = config["sources"]["berd_rd"]
    capex_workbook = load_official_workbook(
        client,
        str(capex_source["url"]),
        root / str(capex_source["bundled_seed"]),
    )
    rd_workbook = load_official_workbook(
        client,
        str(rd_source["url"]),
        root / str(rd_source["bundled_seed"]),
    )
    capex_rows = parse_aies_capex(capex_workbook.payload)
    prior_year = int(rd_source["prior_year"])
    current_year = int(rd_source["reference_year"])
    rd_rows = parse_berd_rd(rd_workbook.payload, prior_year, current_year)
    if not capex_rows:
        raise V32Error("AIES CAPEX workbook produced no usable rows")
    if not rd_rows:
        raise V32Error("BERD workbook produced no usable rows")

    phase2 = _load_phase2(root)
    raw_by_theme: dict[str, dict[str, Any]] = {}
    for item in themes:
        theme_id = str(item["theme_id"])
        target_codes = [str(code) for code in item["qcew_naics"]]
        selected_capex = select_naics_rows(capex_rows, target_codes)
        selected_rd = select_naics_rows(rd_rows, target_codes)
        raw_by_theme[theme_id] = {
            "item": item,
            "phase2": (phase2.get(theme_id) or {}).get("phase2_data_signal_score"),
            "capex": aggregate_capex(selected_capex),
            "rd": aggregate_rd(selected_rd, prior_year, current_year),
        }

    capex_totals = {
        theme_id: row["capex"].get("total_capex_thousand_usd")
        for theme_id, row in raw_by_theme.items()
    }
    rd_totals = {
        theme_id: row["rd"].get(f"rd_million_usd_{current_year}")
        for theme_id, row in raw_by_theme.items()
    }
    capex_scale = percentile_scores(capex_totals)
    rd_scale = percentile_scores(rd_totals)
    weights = config["weights"]

    output_rows: list[dict[str, Any]] = []
    for theme_id, raw in raw_by_theme.items():
        item = raw["item"]
        capex = dict(raw["capex"])
        rd = dict(raw["rd"])

        capex_total = capex.get("total_capex_thousand_usd")
        capex_cv = capex.get("coefficient_of_variation_pct")
        if capex_total is not None:
            reliability = 50.0 if capex_cv is None else max(0.0, min(100.0, 100.0 - 2.0 * float(capex_cv)))
            capex_score = round(
                float(weights["capex_scale"]) * float(capex_scale[theme_id])
                + float(weights["capex_reliability"]) * reliability,
                4,
            )
        else:
            reliability = None
            capex_score = None
        capex.update({
            "status": capex_workbook.status if capex_score is not None else "MAPPING_NO_MATCH",
            "reference_year": int(capex_source["reference_year"]),
            "scale_percentile_score": capex_scale[theme_id],
            "reliability_score": None if reliability is None else round(reliability, 4),
            "source_signal_score": capex_score,
        })

        growth_score = bounded_growth_score(rd.get("rd_growth_ratio"))
        scale_score = rd_scale[theme_id]
        rd_score = _weighted_available([
            (float(weights["rd_growth"]), growth_score),
            (float(weights["rd_scale"]), scale_score),
        ])
        rd.update({
            "status": rd_workbook.status if rd_score is not None else "MAPPING_NO_MATCH",
            "prior_year": prior_year,
            "reference_year": current_year,
            "growth_score": growth_score,
            "scale_percentile_score": scale_score,
            "source_signal_score": rd_score,
        })

        phase2_score = raw["phase2"]
        phase3_score = _weighted_available([
            (float(weights["phase2_real_economy"]), phase2_score),
            (float(weights["capex_investment"]), capex_score),
            (float(weights["business_rd"]), rd_score),
        ])
        observed_count = sum(value is not None for value in (phase2_score, capex_score, rd_score))
        status = "PHASE3_OBSERVED" if observed_count == 3 else (
            "PHASE3_PARTIAL" if observed_count >= 1 else "NO_SOURCE_DATA"
        )

        output_rows.append({
            "theme_id": theme_id,
            "theme_name": item["theme_name"],
            "sector": item["sector"],
            "data_build_priority": item["data_build_priority"],
            "status": status,
            "phase2_data_signal_score": phase2_score,
            "capex_investment_signal_score": capex_score,
            "business_rd_signal_score": rd_score,
            "phase3_investment_signal_score": phase3_score,
            "boom_score": None,
            "frozen_model_score_eligible": False,
            "sources": {
                "aies_capex": capex,
                "berd_business_rd": rd,
            },
            "mapping": {
                "scope": item["mapping_scope"],
                "naics_proxy_basket": item["qcew_naics"],
                "version": item["mapping_version"],
            },
            "limitations": [
                "AIES·BERD 산업자료는 테마별 대표기업 재무제표가 아니라 NAICS 프록시입니다.",
                "2023 AIES CAPEX는 실험통계이며 전년도 ACES와 직접 성장률 비교하지 않습니다.",
                "BERD R&D는 공시기업뿐 아니라 비상장기업을 포함한 미국 산업 추정치입니다.",
                "phase3_investment_signal_score는 최종 산업 붐 점수가 아닙니다.",
            ],
        })

    output_rows.sort(key=lambda row: (int(row["data_build_priority"]), str(row["theme_id"])))
    observations = {
        "schema_version": 1,
        "engine_release": config["engine_release"],
        "as_of": as_of.isoformat(),
        "captured_at": captured_at,
        "theme_count": len(output_rows),
        "source_provenance": {
            "aies_capex": {
                **capex_source,
                "status": capex_workbook.status,
                "sha256": capex_workbook.sha256,
                "live_error": capex_workbook.source_error,
            },
            "berd_rd": {
                **rd_source,
                "status": rd_workbook.status,
                "sha256": rd_workbook.sha256,
                "live_error": rd_workbook.source_error,
            },
        },
        "investment_use_allowed": False,
        "themes": output_rows,
    }
    observations["content_sha256"] = canonical_sha256(observations)

    ranked = sorted(
        [row for row in output_rows if row["phase3_investment_signal_score"] is not None],
        key=lambda row: (-float(row["phase3_investment_signal_score"]), str(row["theme_id"])),
    )
    ranking = [
        {
            "rank": index,
            "theme_id": row["theme_id"],
            "theme_name": row["theme_name"],
            "sector": row["sector"],
            "phase3_investment_signal_score": row["phase3_investment_signal_score"],
            "phase2_data_signal_score": row["phase2_data_signal_score"],
            "capex_investment_signal_score": row["capex_investment_signal_score"],
            "business_rd_signal_score": row["business_rd_signal_score"],
            "boom_score": None,
            "warning": "실물경제·CAPEX·기업 R&D 통합 데이터 신호이며 최종 붐 점수가 아닙니다.",
        }
        for index, row in enumerate(ranked, start=1)
    ]

    phase3_observed = sum(1 for row in output_rows if row["status"] == "PHASE3_OBSERVED")
    capex_observed = sum(1 for row in output_rows if row["capex_investment_signal_score"] is not None)
    rd_observed = sum(1 for row in output_rows if row["business_rd_signal_score"] is not None)
    summary = {
        "status": "V3_2_CORPORATE_INVESTMENT_COLLECTED",
        "engine_release": config["engine_release"],
        "as_of": as_of.isoformat(),
        "theme_count": len(output_rows),
        "phase3_observed_theme_count": phase3_observed,
        "capex_observed_theme_count": capex_observed,
        "business_rd_observed_theme_count": rd_observed,
        "source_status": {
            "aies_capex": capex_workbook.status,
            "berd_business_rd": rd_workbook.status,
        },
        "model_lock": model_lock,
        "frozen_boom_score_new_theme_count": 0,
        "investment_use_allowed": False,
        "next_required_gate": "ADD_REVENUE_CONVERSION_SUPPLY_CHAIN_AND_PROSPECTIVE_OUTCOMES_BEFORE_FROZEN_BOOM_SCORING",
        "limitations": [
            "CAPEX는 2023 산업별 수준·신뢰도 신호이며 시계열 증가율 신호가 아닙니다.",
            "R&D는 2022~2023 증가율과 2023 규모를 함께 반영합니다.",
            "최종 boom_score는 계속 null로 유지합니다.",
        ],
    }
    health = {
        "status": "SOURCE_HEALTH_RECORDED",
        "sources": observations["source_provenance"],
        "usable_rows": {"aies_capex": len(capex_rows), "berd_rd": len(rd_rows)},
        "observed_themes": {"capex": capex_observed, "business_rd": rd_observed, "phase3": phase3_observed},
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "v32_run_summary.json", summary)
    write_json(output_dir / "v32_corporate_investment_observations.json", observations)
    write_json(output_dir / "v32_phase3_investment_signal_ranking.json", {
        "status": "NOT_A_BOOM_SCORE_RANKING",
        "as_of": as_of.isoformat(),
        "ranking": ranking,
    })
    write_json(output_dir / "v32_source_health.json", health)
    write_json(output_dir / "v32_model_lock_verification.json", model_lock)
    write_json(output_dir / "v32_next_gate.json", {
        "status": "V3_2_COMPLETE",
        "next_required_gate": summary["next_required_gate"],
        "investment_use_allowed": False,
    })

    dated = root / "data_cache" / f"{as_of.year:04d}" / f"{as_of.month:02d}" / as_of.isoformat() / "v32_corporate_investment_observations.json"
    latest = root / "data_cache/latest/v32_corporate_investment_observations.json"
    write_json(dated, observations)
    write_json(latest, observations)
    return summary
