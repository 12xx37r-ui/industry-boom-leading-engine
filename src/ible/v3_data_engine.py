from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ible.integrity import canonical_sha256, load_json, write_json
from ible.model_lock import load_and_verify_model_lock
from ible.v3_collectors import OpenAlexCollector, UsaSpendingCollector, comparison_periods
from ible.v3_http import HttpError, HttpSettings, JsonHttpClient


class V3DataError(RuntimeError):
    pass


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def source_signal(recent: int, prior: int) -> dict[str, float]:
    ratio = (recent + 1.0) / (prior + 1.0)
    growth_pct = 100.0 * ((recent - prior) / max(1.0, float(prior)))
    growth_score = 50.0 + 35.0 * math.tanh(math.log(ratio) / 1.25)
    scale_score = min(100.0, 18.0 * math.log10(recent + 1.0))
    signal = clamp(0.60 * growth_score + 0.40 * scale_score)
    return {
        "recent_count": int(recent),
        "prior_count": int(prior),
        "growth_percent": round(growth_pct, 4),
        "growth_score": round(clamp(growth_score), 4),
        "scale_score": round(scale_score, 4),
        "source_signal_score": round(signal, 4),
    }


def _load_cache(root: Path) -> dict[str, Any] | None:
    path = root / "data_cache/latest/v3_source_observations.json"
    if not path.is_file():
        return None
    try:
        payload = load_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _cached_source(cache: dict[str, Any] | None, theme_id: str, source: str) -> dict[str, Any] | None:
    if not cache:
        return None
    for row in cache.get("themes") or []:
        if row.get("theme_id") == theme_id:
            candidate = (row.get("sources") or {}).get(source)
            return candidate if isinstance(candidate, dict) else None
    return None


def _collect_one(
    row: dict[str, Any],
    openalex: OpenAlexCollector,
    usaspending: UsaSpendingCollector,
    recent_period: Any,
    prior_period: Any,
    cache: dict[str, Any] | None,
    captured_at: str,
) -> dict[str, Any]:
    theme_id = str(row["theme_id"])
    sources: dict[str, Any] = {}
    errors: list[dict[str, str]] = []

    for source_name, collector_call, query in (
        (
            "openalex",
            lambda: (
                openalex.count(str(row["openalex_search"]), recent_period),
                openalex.count(str(row["openalex_search"]), prior_period),
            ),
            str(row["openalex_search"]),
        ),
        (
            "usaspending",
            lambda: (
                usaspending.count(list(row["usaspending_keywords"]), recent_period),
                usaspending.count(list(row["usaspending_keywords"]), prior_period),
            ),
            list(row["usaspending_keywords"]),
        ),
    ):
        try:
            recent, prior = collector_call()
            sources[source_name] = {
                "status": "LIVE_COLLECTED",
                "query": query,
                "captured_at": captured_at,
                "recent_period": recent_period.as_dict(),
                "prior_period": prior_period.as_dict(),
                **source_signal(recent, prior),
            }
        except (HttpError, ValueError, TypeError) as exc:
            cached = _cached_source(cache, theme_id, source_name)
            if cached:
                fallback = dict(cached)
                fallback["status"] = "CACHE_FALLBACK"
                fallback["fallback_reason"] = str(exc)[:500]
                sources[source_name] = fallback
            else:
                sources[source_name] = {
                    "status": "SOURCE_UNAVAILABLE",
                    "query": query,
                    "captured_at": captured_at,
                    "recent_period": recent_period.as_dict(),
                    "prior_period": prior_period.as_dict(),
                    "recent_count": None,
                    "prior_count": None,
                    "growth_percent": None,
                    "growth_score": None,
                    "scale_score": None,
                    "source_signal_score": None,
                }
            errors.append({"source": source_name, "error": str(exc)[:500]})

    available = [
        source for source in sources.values()
        if source.get("status") in {"LIVE_COLLECTED", "CACHE_FALLBACK"}
        and source.get("source_signal_score") is not None
    ]
    phase1_score = round(sum(float(x["source_signal_score"]) for x in available) / len(available), 4) if available else None
    status = "PHASE1_OBSERVED" if len(available) >= 2 else ("PARTIAL_SOURCE" if available else "NO_SOURCE_DATA")
    return {
        "theme_id": theme_id,
        "theme_name": row["theme_name"],
        "sector": row["sector"],
        "data_build_priority": row["data_build_priority"],
        "status": status,
        "source_family_count": len(available),
        "phase1_data_signal_score": phase1_score,
        "boom_score": None,
        "frozen_model_score_eligible": False,
        "sources": sources,
        "errors": errors,
        "limitations": [
            "Phase 1 신호는 연구 및 미국 연방정부 수상·계약 건수만 반영합니다.",
            "CAPEX·기업 R&D·채용·매출 전환·영업생존력 자료가 없어 V0.9.1 boom_score로 변환하지 않습니다.",
        ],
    }


def run_v3_data(root: Path, output_dir: Path, run_date: str | None = None) -> dict[str, Any]:
    source_config = load_json(root / "config/v3_data_sources.json")
    query_config = load_json(root / "config/v3_theme_queries.json")
    model_lock = load_and_verify_model_lock(root)
    if len(query_config.get("themes") or []) != int(source_config["minimum_theme_count"]):
        raise V3DataError("theme query count mismatch")

    timezone = ZoneInfo(str(source_config.get("timezone") or "Asia/Seoul"))
    now = datetime.now(timezone)
    today = date.fromisoformat(run_date) if run_date else now.date()
    as_of = today - timedelta(days=1)
    recent_period, prior_period = comparison_periods(as_of, int(source_config["lookback_days"]))
    captured_at = now.isoformat(timespec="seconds")

    network = source_config["network"]
    client = JsonHttpClient(HttpSettings(
        timeout_seconds=int(network["timeout_seconds"]),
        max_attempts=int(network["max_attempts"]),
        base_backoff_seconds=float(network["base_backoff_seconds"]),
        user_agent=str(network["user_agent"]),
    ))
    openalex = OpenAlexCollector(client, str(source_config["sources"]["openalex"]["base_url"]))
    usaspending = UsaSpendingCollector(client, str(source_config["sources"]["usaspending"]["base_url"]))
    cache = _load_cache(root)

    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=int(network["max_workers"])) as executor:
        futures = {
            executor.submit(_collect_one, row, openalex, usaspending, recent_period, prior_period, cache, captured_at): row
            for row in query_config["themes"]
        }
        for future in as_completed(futures):
            rows.append(future.result())
    rows.sort(key=lambda x: (int(x["data_build_priority"]), str(x["theme_id"])))

    observations = {
        "schema_version": 1,
        "engine_release": source_config["engine_release"],
        "as_of": as_of.isoformat(),
        "captured_at": captured_at,
        "recent_period": recent_period.as_dict(),
        "prior_period": prior_period.as_dict(),
        "theme_count": len(rows),
        "source_provenance": source_config["sources"],
        "revision_risk": "Public APIs may revise historical records after capture; committed cache preserves what this run received.",
        "investment_use_allowed": False,
        "themes": rows,
    }
    observations["content_sha256"] = canonical_sha256(observations)

    observed = [x for x in rows if x["status"] == "PHASE1_OBSERVED"]
    partial = [x for x in rows if x["status"] == "PARTIAL_SOURCE"]
    unavailable = [x for x in rows if x["status"] == "NO_SOURCE_DATA"]
    live_cells = sum(1 for x in rows for s in x["sources"].values() if s.get("status") == "LIVE_COLLECTED")
    cache_cells = sum(1 for x in rows for s in x["sources"].values() if s.get("status") == "CACHE_FALLBACK")
    failed_cells = sum(1 for x in rows for s in x["sources"].values() if s.get("status") == "SOURCE_UNAVAILABLE")

    ranked_phase1 = sorted(
        [x for x in rows if x["phase1_data_signal_score"] is not None],
        key=lambda x: (-float(x["phase1_data_signal_score"]), str(x["theme_id"])),
    )
    phase1_ranking = [{
        "rank": index,
        "theme_id": row["theme_id"],
        "theme_name": row["theme_name"],
        "sector": row["sector"],
        "phase1_data_signal_score": row["phase1_data_signal_score"],
        "source_family_count": row["source_family_count"],
        "boom_score": None,
        "warning": "데이터 수집 우선순위용 신호이며 V0.9.1 산업 붐 점수가 아닙니다.",
    } for index, row in enumerate(ranked_phase1, start=1)]

    summary_status = "V3_PHASE1_LIVE_DATA_COLLECTED" if observed else "V3_PHASE1_NO_LIVE_OR_CACHED_DATA"
    summary = {
        "status": summary_status,
        "engine_release": source_config["engine_release"],
        "as_of": as_of.isoformat(),
        "theme_count": len(rows),
        "phase1_observed_theme_count": len(observed),
        "partial_theme_count": len(partial),
        "unavailable_theme_count": len(unavailable),
        "phase1_observation_coverage_percent": round(100.0 * len(observed) / max(1, len(rows)), 2),
        "source_cells": {"live": live_cells, "cache_fallback": cache_cells, "unavailable": failed_cells},
        "frozen_boom_score_new_theme_count": 0,
        "model_lock": model_lock,
        "investment_use_allowed": False,
        "next_required_gate": "ADD_CORPORATE_CAPEX_RD_HIRING_REVENUE_VIABILITY_SOURCES_BEFORE_FROZEN_BOOM_SCORING",
        "limitations": [
            "OpenAlex는 글로벌 연구 확산, USAspending은 미국 연방 지출·수상 건수만 포착합니다.",
            "공공 API 과거 값은 사후 수정될 수 있어 이번 실행 응답을 GitHub 캐시로 봉인합니다.",
            "두 출처만으로는 최종 산업 붐 점수를 산출하지 않습니다.",
        ],
    }

    health = {
        "status": "SOURCE_HEALTH_RECORDED",
        "as_of": as_of.isoformat(),
        "source_cells": summary["source_cells"],
        "themes_with_errors": sum(1 for x in rows if x["errors"]),
        "errors": [
            {"theme_id": x["theme_id"], "errors": x["errors"]}
            for x in rows if x["errors"]
        ],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "v3_run_summary.json", summary)
    write_json(output_dir / "v3_source_observations.json", observations)
    write_json(output_dir / "v3_phase1_data_signal_ranking.json", {
        "status": "NOT_A_BOOM_SCORE_RANKING",
        "as_of": as_of.isoformat(),
        "ranking": phase1_ranking,
    })
    write_json(output_dir / "v3_data_source_health.json", health)
    write_json(output_dir / "v3_model_lock_verification.json", model_lock)
    write_json(output_dir / "v3_next_gate.json", {
        "status": "V3_PHASE1_COMPLETE_OR_RETRYABLE",
        "next_required_gate": summary["next_required_gate"],
        "investment_use_allowed": False,
    })

    dated_cache = root / "data_cache" / f"{as_of.year:04d}" / f"{as_of.month:02d}" / as_of.isoformat() / "v3_source_observations.json"
    latest_cache = root / "data_cache/latest/v3_source_observations.json"
    write_json(dated_cache, observations)
    write_json(latest_cache, observations)
    return summary
