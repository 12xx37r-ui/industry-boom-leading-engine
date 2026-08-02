from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ible.integrity import canonical_sha256, load_json, write_json
from ible.model_lock import load_and_verify_model_lock
from ible.v3_http import HttpError, HttpSettings, JsonHttpClient
from ible.v31_qcew import QcewCollector, aggregate_naics, qcew_signal


class V31Error(RuntimeError):
    pass


def _load_phase1(root: Path) -> dict[str, dict[str, Any]]:
    path = root / "data_cache/latest/v3_source_observations.json"
    if not path.is_file():
        return {}
    try:
        payload = load_json(path)
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(row.get("theme_id")): row for row in payload.get("themes") or [] if row.get("theme_id")}


def _load_cache(root: Path) -> dict[str, Any] | None:
    path = root / "data_cache/latest/v31_real_economy_observations.json"
    if not path.is_file():
        return None
    try:
        payload = load_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _cached_theme(cache: dict[str, Any] | None, theme_id: str) -> dict[str, Any] | None:
    for row in (cache or {}).get("themes") or []:
        if row.get("theme_id") == theme_id:
            return row
    return None


def run_v31(root: Path, output_dir: Path, run_date: str | None = None) -> dict[str, Any]:
    config = load_json(root / "config/v31_real_economy_sources.json")
    mapping = load_json(root / "config/v31_theme_naics.json")
    if len(mapping.get("themes") or []) != int(config["minimum_theme_count"]):
        raise V31Error("theme NAICS mapping count mismatch")
    model_lock = load_and_verify_model_lock(root)
    timezone = ZoneInfo(str(config.get("timezone") or "Asia/Seoul"))
    now = datetime.now(timezone)
    today = date.fromisoformat(run_date) if run_date else now.date()
    as_of = today - timedelta(days=1)
    captured_at = now.isoformat(timespec="seconds")

    net = config["network"]
    client = JsonHttpClient(HttpSettings(
        timeout_seconds=int(net["timeout_seconds"]),
        max_attempts=int(net["max_attempts"]),
        base_backoff_seconds=float(net["base_backoff_seconds"]),
        user_agent=str(net["user_agent"]),
    ))
    src = config["source"]
    collector = QcewCollector(
        client,
        str(src["base_url_template"]),
        ownership_code=str(src["ownership_code"]),
        area_fips=str(src["area_fips"]),
        minimum_valid_rows=int(net["minimum_valid_csv_rows"]),
    )
    phase1 = _load_phase1(root)
    cache = _load_cache(root)
    source_error: str | None = None
    recent_rows: list[dict[str, str]] = []
    prior_rows: list[dict[str, str]] = []
    quarter_label: str | None = None
    try:
        quarter, recent_rows, prior_rows = collector.latest_pair(as_of, int(src["maximum_quarter_probes"]))
        quarter_label = quarter.label
    except HttpError as exc:
        source_error = str(exc)[:1200]

    rows: list[dict[str, Any]] = []
    for item in mapping["themes"]:
        theme_id = str(item["theme_id"])
        phase1_row = phase1.get(theme_id) or {}
        phase1_score = phase1_row.get("phase1_data_signal_score")
        qcew: dict[str, Any]
        if recent_rows and prior_rows:
            recent = aggregate_naics(recent_rows, list(item["qcew_naics"]))
            prior = aggregate_naics(prior_rows, list(item["qcew_naics"]))
            if recent["matched_naics_count"] > 0 and prior["matched_naics_count"] > 0:
                qcew = {
                    "status": "LIVE_COLLECTED",
                    "captured_at": captured_at,
                    "quarter": quarter_label,
                    "prior_year_quarter": f"{int(quarter_label[:4]) - 1}{quarter_label[4:]}",
                    "qcew_naics": item["qcew_naics"],
                    **qcew_signal(recent, prior, config["weights"]),
                }
            else:
                qcew = {"status": "MAPPING_NO_MATCH", "qcew_naics": item["qcew_naics"], "source_signal_score": None}
        else:
            cached = _cached_theme(cache, theme_id)
            cached_qcew = ((cached or {}).get("sources") or {}).get("qcew")
            if isinstance(cached_qcew, dict) and cached_qcew.get("source_signal_score") is not None:
                qcew = dict(cached_qcew)
                qcew["status"] = "CACHE_FALLBACK"
                qcew["fallback_reason"] = source_error
            else:
                qcew = {"status": "SOURCE_UNAVAILABLE", "qcew_naics": item["qcew_naics"], "source_signal_score": None, "error": source_error}

        qcew_score = qcew.get("source_signal_score")
        if phase1_score is not None and qcew_score is not None:
            phase2 = round(
                float(config["weights"]["phase1_public_signal"]) * float(phase1_score)
                + float(config["weights"]["qcew_real_economy_signal"]) * float(qcew_score),
                4,
            )
            status = "PHASE2_OBSERVED"
        elif qcew_score is not None:
            phase2 = round(float(qcew_score), 4)
            status = "REAL_ECONOMY_ONLY"
        elif phase1_score is not None:
            phase2 = round(float(phase1_score), 4)
            status = "PUBLIC_SIGNAL_ONLY"
        else:
            phase2 = None
            status = "NO_SOURCE_DATA"
        rows.append({
            "theme_id": theme_id,
            "theme_name": item["theme_name"],
            "sector": item["sector"],
            "data_build_priority": item["data_build_priority"],
            "status": status,
            "phase1_data_signal_score": phase1_score,
            "qcew_real_economy_signal_score": qcew_score,
            "phase2_data_signal_score": phase2,
            "boom_score": None,
            "frozen_model_score_eligible": False,
            "sources": {"qcew": qcew},
            "mapping": {"scope": item["mapping_scope"], "qcew_naics": item["qcew_naics"], "version": item["mapping_version"]},
            "limitations": [
                "QCEW NAICS 바스켓은 테마의 미국 산업 프록시이며 개별 기업 매출을 뜻하지 않습니다.",
                "기업 CAPEX·기업 R&D·매출 전환·영업생존력은 아직 연결되지 않았습니다.",
                "phase2_data_signal_score는 최종 산업 붐 점수가 아닙니다.",
            ],
        })

    rows.sort(key=lambda x: (int(x["data_build_priority"]), str(x["theme_id"])))
    observations = {
        "schema_version": 1,
        "engine_release": config["engine_release"],
        "as_of": as_of.isoformat(),
        "captured_at": captured_at,
        "qcew_quarter": quarter_label,
        "theme_count": len(rows),
        "source_provenance": src,
        "investment_use_allowed": False,
        "themes": rows,
    }
    observations["content_sha256"] = canonical_sha256(observations)
    ranking_rows = sorted([r for r in rows if r["phase2_data_signal_score"] is not None], key=lambda r: (-float(r["phase2_data_signal_score"]), str(r["theme_id"])))
    ranking = [{
        "rank": idx,
        "theme_id": row["theme_id"],
        "theme_name": row["theme_name"],
        "sector": row["sector"],
        "phase2_data_signal_score": row["phase2_data_signal_score"],
        "qcew_real_economy_signal_score": row["qcew_real_economy_signal_score"],
        "boom_score": None,
        "warning": "연구·정부지출·고용·사업체·임금 기반 데이터 신호이며 최종 붐 점수가 아닙니다.",
    } for idx, row in enumerate(ranking_rows, start=1)]

    live = sum(1 for r in rows if r["sources"]["qcew"].get("status") == "LIVE_COLLECTED")
    fallback = sum(1 for r in rows if r["sources"]["qcew"].get("status") == "CACHE_FALLBACK")
    unavailable = len(rows) - live - fallback
    phase2_count = sum(1 for r in rows if r["status"] == "PHASE2_OBSERVED")
    summary = {
        "status": "V3_1_REAL_ECONOMY_COLLECTED" if live or fallback else "V3_1_REAL_ECONOMY_UNAVAILABLE",
        "engine_release": config["engine_release"],
        "as_of": as_of.isoformat(),
        "qcew_quarter": quarter_label,
        "theme_count": len(rows),
        "phase2_observed_theme_count": phase2_count,
        "qcew_source_cells": {"live": live, "cache_fallback": fallback, "unavailable": unavailable},
        "model_lock": model_lock,
        "frozen_boom_score_new_theme_count": 0,
        "investment_use_allowed": False,
        "next_required_gate": "ADD_CORPORATE_CAPEX_RD_REVENUE_AND_VIABILITY_BEFORE_FROZEN_BOOM_SCORING",
        "limitations": [
            "QCEW는 미국 민간 사업체의 산업별 고용·사업체·임금 프록시입니다.",
            "테마와 NAICS는 일대일 대응이 아니므로 바스켓 매핑 한계가 있습니다.",
            "최종 boom_score는 계속 null로 유지합니다.",
        ],
    }
    health = {
        "status": "SOURCE_HEALTH_RECORDED",
        "source": "BLS_QCEW",
        "qcew_quarter": quarter_label,
        "source_error": source_error,
        "source_cells": summary["qcew_source_cells"],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "v31_run_summary.json", summary)
    write_json(output_dir / "v31_real_economy_observations.json", observations)
    write_json(output_dir / "v31_phase2_data_signal_ranking.json", {"status": "NOT_A_BOOM_SCORE_RANKING", "as_of": as_of.isoformat(), "ranking": ranking})
    write_json(output_dir / "v31_source_health.json", health)
    write_json(output_dir / "v31_model_lock_verification.json", model_lock)
    write_json(output_dir / "v31_next_gate.json", {"status": "V3_1_COMPLETE_OR_RETRYABLE", "next_required_gate": summary["next_required_gate"], "investment_use_allowed": False})

    dated = root / "data_cache" / f"{as_of.year:04d}" / f"{as_of.month:02d}" / as_of.isoformat() / "v31_real_economy_observations.json"
    latest = root / "data_cache/latest/v31_real_economy_observations.json"
    write_json(dated, observations)
    write_json(latest, observations)
    return summary
