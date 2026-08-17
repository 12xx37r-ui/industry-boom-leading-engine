from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ible.config import load_yaml
from ible.integrity import canonical_sha256, load_json, write_json
from ible.v3_dynamic_terms import discover_candidates
from ible.v50_outcomes import add_months, finite, pearson_correlation, percent_change, weighted_available


class V8Error(RuntimeError):
    pass


def _clamp(value: Any, lower: float = 0.0, upper: float = 100.0) -> float:
    try:
        return max(lower, min(upper, float(value)))
    except (TypeError, ValueError):
        return lower


def _theme_map(payload: dict[str, Any] | None, key: str = "themes") -> dict[str, dict[str, Any]]:
    return {
        str(row.get("theme_id")): row
        for row in (payload or {}).get(key) or []
        if row.get("theme_id")
    }


def _load_optional(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    return load_json(path) if path.is_file() else {}


def _date_from(value: Any, fallback: date) -> date:
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return fallback


def _observed_date(row: dict[str, Any], payload_as_of: str, fallback: date) -> date:
    for key in ("as_of", "latest_period", "quarter"):
        value = row.get(key)
        if key == "quarter" and value:
            try:
                return date(int(str(value)[:4]), 12 if str(value)[-2:] == "Q4" else 9, 28)
            except (TypeError, ValueError):
                pass
        parsed = _date_from(value, fallback)
        if value and parsed != fallback:
            return parsed
    reference_year = finite(row.get("reference_year"))
    if reference_year is not None:
        return date(int(reference_year), 12, 31)
    return _date_from(payload_as_of, fallback)


def _source_meta(config: dict[str, Any], key: str) -> dict[str, float]:
    defaults = (config.get("proxy_quality") or {}).get("source_defaults") or {}
    return dict(defaults.get(key) or {"directness": 0.5, "publication_lag_days": 90, "revision_risk": 0.4})


def _proxy(
    config: dict[str, Any],
    family: str,
    metric: str,
    source_row: dict[str, Any],
    payload_as_of: str,
    as_of: date,
    status: str | None = None,
    observed_override: Any = None,
) -> dict[str, Any] | None:
    if not source_row or status in {"SOURCE_UNAVAILABLE", "SOURCE_DISABLED", "MAPPING_NO_MATCH"}:
        return None
    meta = _source_meta(config, family)
    observed = _date_from(observed_override, as_of) if observed_override else _observed_date(source_row, payload_as_of, as_of)
    age_days = max(0, (as_of - observed).days)
    half_life = max(1.0, float((config.get("proxy_quality") or {}).get("freshness_half_life_days", 365)))
    freshness = round(math.exp(-age_days / half_life), 6)
    revision_factor = _clamp(1.0 - float(meta.get("revision_risk", 0.4)), 0.0, 1.0)
    quality = round(100.0 * (0.65 * float(meta.get("directness", 0.5)) + 0.25 * freshness + 0.10 * revision_factor), 4)
    return {
        "source_family": family,
        "metric": metric,
        "status": status or str(source_row.get("status") or "OBSERVED"),
        "directness": round(float(meta.get("directness", 0.5)), 4),
        "observed_as_of": observed.isoformat(),
        "publication_lag_days": int(meta.get("publication_lag_days", 90)),
        "revision_risk": round(float(meta.get("revision_risk", 0.4)), 4),
        "age_days": age_days,
        "freshness_weight": freshness,
        "quality_score": quality,
    }


def _proxy_rows(root: Path, theme_id: str, as_of: date, config: dict[str, Any]) -> list[dict[str, Any]]:
    payloads = {
        "v3": _load_optional(root, "data_cache/latest/v3_source_observations.json"),
        "v31": _load_optional(root, "data_cache/latest/v31_real_economy_observations.json"),
        "v32": _load_optional(root, "data_cache/latest/v32_corporate_investment_observations.json"),
        "v33": _load_optional(root, "data_cache/latest/v33_phase4_observations.json"),
        "v40": _load_optional(root, "data_cache/latest/v40_direct_commercialization_observations.json"),
    }
    rows: list[dict[str, Any]] = []
    v3 = _theme_map(payloads["v3"]).get(theme_id) or {}
    v31 = _theme_map(payloads["v31"]).get(theme_id) or {}
    v32 = _theme_map(payloads["v32"]).get(theme_id) or {}
    v33 = _theme_map(payloads["v33"]).get(theme_id) or {}
    v40 = _theme_map(payloads["v40"]).get(theme_id) or {}

    v40_sources = v40.get("sources") or {}
    mapping_quality = str(v40.get("mapping_quality") or "")
    if mapping_quality == "DIRECT_QSS_NAICS_REVENUE":
        item = _proxy(config, "v40_qss_revenue", "commercial_revenue", v40_sources.get("qss_revenue") or {}, str(payloads["v40"].get("as_of")), as_of)
        if item:
            rows.append(item)
    elif mapping_quality and mapping_quality != "NO_DIRECT_REVENUE_COVERAGE":
        item = _proxy(config, "v40_m3_proxy", "shipments_or_orders", v40_sources.get("m3_shipments") or {}, str(payloads["v40"].get("as_of")), as_of)
        if item:
            rows.append(item)

    qcew = (v31.get("sources") or {}).get("qcew") or {}
    item = _proxy(config, "v31_qcew", "employment_establishments_wages", qcew, str(payloads["v31"].get("as_of")), as_of)
    if item:
        rows.append(item)

    investment = v32.get("sources") or {}
    item = _proxy(config, "v32_aies_capex", "industry_capex", investment.get("aies_capex") or {}, str(payloads["v32"].get("as_of")), as_of)
    if item:
        rows.append(item)
    item = _proxy(config, "v32_berd_rd", "business_r_and_d", investment.get("berd_business_rd") or {}, str(payloads["v32"].get("as_of")), as_of)
    if item:
        rows.append(item)

    if v33:
        item = _proxy(config, "v33_phase4", "commercialization_and_diffusion", v33, str(payloads["v33"].get("as_of")), as_of)
        if item:
            rows.append(item)

    source_rows = v3.get("sources") or {}
    for source_name, key, metric in (
        ("openalex", "openalex_research", "research_diffusion"),
        ("usaspending", "usaspending_proxy", "government_funding_proxy"),
        ("naver_search_trend", "naver_interest", "public_interest"),
    ):
        item = _proxy(config, key, metric, source_rows.get(source_name) or {}, str(payloads["v3"].get("as_of")), as_of)
        if item:
            rows.append(item)
    return rows


def proxy_quality_for_theme(root: Path, theme_id: str, as_of: date, config: dict[str, Any]) -> dict[str, Any]:
    proxies = _proxy_rows(root, theme_id, as_of, config)
    observed = [row for row in proxies if row.get("status") not in {"SOURCE_UNAVAILABLE", "SOURCE_DISABLED", "MAPPING_NO_MATCH"}]
    if not observed:
        return {"theme_id": theme_id, "proxy_count": 0, "observed_proxy_count": 0, "proxy_quality_score": 0.0, "directness_score": 0.0, "freshness_score": 0.0, "quality_multiplier": float((config.get("proxy_quality") or {}).get("quality_multiplier_floor", 0.7)), "proxies": [], "warnings": ["관측 가능한 프록시가 없습니다."]}
    directness = sum(row["directness"] for row in observed) / len(observed)
    freshness = sum(row["freshness_weight"] for row in observed) / len(observed)
    quality = sum(row["quality_score"] for row in observed) / len(observed)
    quality_cfg = config.get("proxy_quality") or {}
    floor = float(quality_cfg.get("quality_multiplier_floor", 0.7))
    ceiling = float(quality_cfg.get("quality_multiplier_ceiling", 1.0))
    multiplier = _clamp(floor + (ceiling - floor) * quality / 100.0, floor, ceiling)
    warnings = []
    if len(observed) < int(quality_cfg.get("minimum_observed_proxy_count", 2)):
        warnings.append("관측 프록시 수가 최소 기준보다 적습니다.")
    if directness < 0.6:
        warnings.append("직접 측정이 아닌 산업·키워드 프록시 비중이 높습니다.")
    if freshness < 0.5:
        warnings.append("자료 시차가 커 최신성 가중치가 낮습니다.")
    return {
        "theme_id": theme_id,
        "proxy_count": len(proxies),
        "observed_proxy_count": len(observed),
        "proxy_quality_score": round(quality, 4),
        "directness_score": round(100.0 * directness, 4),
        "freshness_score": round(100.0 * freshness, 4),
        "quality_multiplier": round(multiplier, 6),
        "proxies": proxies,
        "warnings": warnings,
    }


def _value(row: dict[str, Any], *path: str) -> float | None:
    current: Any = row
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return finite(current)


def _outcome_row(theme_id: str, maps: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    v3 = maps["v3"].get(theme_id) or {}
    v31 = maps["v31"].get(theme_id) or {}
    v32 = maps["v32"].get(theme_id) or {}
    v40 = maps["v40"].get(theme_id) or {}
    qcew = (v31.get("sources") or {}).get("qcew") or {}
    capex = (v32.get("sources") or {}).get("aies_capex") or {}
    qss = (v40.get("sources") or {}).get("qss_revenue") or {}
    m3 = (v40.get("sources") or {}).get("m3_shipments") or {}
    openalex = (v3.get("sources") or {}).get("openalex") or {}
    return {
        "theme_id": theme_id,
        "revenue_level": _value(qss, "current_revenue_million_usd") or _value(m3, "latest_value_million_usd"),
        "employment_level": _value(qcew, "recent", "employment"),
        "capex_level": _value(capex, "total_capex_thousand_usd"),
        "stock_price": None,
        "industry_growth_percent": _value(qcew, "employment_growth_percent") or _value(v40, "components", "primary_growth_percent"),
        "research_growth_percent": _value(openalex, "growth_percent"),
        "hiring_growth_percent": None,
        "source_vintages": {
            "revenue": v40.get("as_of"),
            "employment": v31.get("as_of"),
            "capex": capex.get("reference_year"),
            "industry_growth": v31.get("as_of"),
        },
    }


def _mean_available(values: list[Any], default: float = 0.0) -> float:
    numbers = [finite(value) for value in values]
    observed = [value for value in numbers if value is not None]
    return sum(observed) / len(observed) if observed else default


def _hidden_interaction(
    root: Path,
    item: dict[str, Any],
    theme_id: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    v3_payload = _load_optional(root, "data_cache/latest/v3_source_observations.json")
    v3 = _theme_map(v3_payload).get(theme_id) or {}
    v31 = _theme_map(_load_optional(root, "data_cache/latest/v31_real_economy_observations.json")).get(theme_id) or {}
    v32 = _theme_map(_load_optional(root, "data_cache/latest/v32_corporate_investment_observations.json")).get(theme_id) or {}
    v33 = _theme_map(_load_optional(root, "data_cache/latest/v33_phase4_observations.json")).get(theme_id) or {}
    v40 = _theme_map(_load_optional(root, "data_cache/latest/v40_direct_commercialization_observations.json")).get(theme_id) or {}
    qcew = (v31.get("sources") or {}).get("qcew") or {}
    capex = (v32.get("sources") or {}).get("aies_capex") or {}
    rd = (v32.get("sources") or {}).get("berd_business_rd") or {}
    openalex = (v3.get("sources") or {}).get("openalex") or {}
    hiring = (v3_payload.get("hiring_nowcast") or {}).get("themes") or []
    hiring_row = next((row for row in hiring if str(row.get("theme_id")) == theme_id), {})
    hiring_score = hiring_row.get("hiring_signal_score") or hiring_row.get("score")
    real_economy = _mean_available([
        qcew.get("source_signal_score"),
        v33.get("commercialization_proxy_score"),
        v40.get("direct_commercialization_score"),
    ])
    capital = _mean_available([
        capex.get("source_signal_score"),
        v32.get("phase3_investment_signal_score"),
    ])
    diffusion = _mean_available([
        v33.get("supply_chain_diffusion_proxy_score"),
        v33.get("components", {}).get("cross_source_diffusion_score"),
        item.get("source_diffusion_percent"),
    ])
    research_or_hiring = _mean_available([
        v33.get("components", {}).get("research"),
        openalex.get("source_signal_score"),
        hiring_score,
    ])
    interest = finite(item.get("public_interest_score"))
    rules = config.get("hidden_interaction") or {}
    minimums = [
        real_economy >= float(rules.get("minimum_real_economy_score", 55.0)),
        capital >= float(rules.get("minimum_capital_score", 50.0)),
        diffusion >= float(rules.get("minimum_diffusion_score", 50.0)),
        research_or_hiring >= float(rules.get("minimum_research_or_hiring_score", 50.0)),
    ]
    low_attention = interest is not None and interest <= float(rules.get("public_interest_max", 45.0))
    eligible = bool(low_attention and all(minimums))
    if eligible:
        activity = _mean_available([real_economy, capital, diffusion, research_or_hiring], 50.0)
        attention_gap = _clamp((float(rules.get("public_interest_max", 45.0)) - float(interest)) / max(1.0, float(rules.get("public_interest_max", 45.0))) * 100.0)
        bonus_cap = float(rules.get("bonus_cap", 15.0))
        bonus = round(min(bonus_cap, bonus_cap * attention_gap / 100.0 * _clamp((activity - 50.0) / 50.0, 0.0, 1.0)), 4)
    else:
        activity = _mean_available([real_economy, capital, diffusion, research_or_hiring], 0.0)
        attention_gap = 0.0
        bonus = 0.0
    base_hidden = finite(item.get("hidden_opportunity_score"))
    hidden_score = None if base_hidden is None else round(_clamp(float(rules.get("base_weight", 0.75)) * base_hidden + float(rules.get("interaction_weight", 0.25)) * (base_hidden + bonus)), 4)
    return {
        "base_hidden_score": base_hidden,
        "hidden_opportunity_score_v8": hidden_score,
        "interaction_bonus": bonus,
        "eligible": eligible,
        "low_attention": low_attention,
        "attention_gap_score": round(attention_gap, 4),
        "real_economy_score": round(real_economy, 4),
        "capital_score": round(capital, 4),
        "diffusion_score": round(diffusion, 4),
        "research_or_hiring_score": round(research_or_hiring, 4),
        "activity_score": round(activity, 4),
        "rule": "LOW_PUBLIC_INTEREST_AND_REAL_ECONOMY_AND_CAPITAL_AND_RESEARCH_OR_HIRING",
    }


def build_outcome_snapshot(root: Path, as_of: date) -> dict[str, Any]:
    input_path = root / "data_cache/inbox/v8_outcomes.json"
    if input_path.is_file():
        payload = load_json(input_path)
        rows = payload if isinstance(payload, list) else list((payload or {}).get("themes") or [])
        return {"schema_version": 1, "as_of": str((payload or {}).get("as_of") or as_of) if isinstance(payload, dict) else as_of.isoformat(), "source": "INBOX_V8_OUTCOMES", "themes": rows}
    payloads = {name: _load_optional(root, f"data_cache/latest/{name}.json") for name in ("v3_source_observations", "v31_real_economy_observations", "v32_corporate_investment_observations", "v40_direct_commercialization_observations")}
    maps = {
        "v3": _theme_map(payloads["v3_source_observations"]),
        "v31": _theme_map(payloads["v31_real_economy_observations"]),
        "v32": _theme_map(payloads["v32_corporate_investment_observations"]),
        "v40": _theme_map(payloads["v40_direct_commercialization_observations"]),
    }
    theme_ids = sorted(set().union(*(set(mapping) for mapping in maps.values())))
    return {"schema_version": 1, "as_of": as_of.isoformat(), "source": "DERIVED_FROM_LATEST_ENGINE_SOURCES", "themes": [_outcome_row(theme_id, maps) for theme_id in theme_ids]}


def _growth_metric(current: Any, baseline: Any) -> float | None:
    return percent_change(current, baseline)


def _scalar_score(value: Any) -> float | None:
    number = finite(value)
    return None if number is None else _clamp(50.0 + 1.5 * number)


def _evaluate_outcome(baseline: dict[str, Any], current: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    changes = {
        "revenue_growth": _growth_metric(current.get("revenue_level"), baseline.get("revenue_level")),
        "employment_growth": _growth_metric(current.get("employment_level"), baseline.get("employment_level")),
        "capex_growth": _growth_metric(current.get("capex_level"), baseline.get("capex_level")),
        "stock_return": _growth_metric(current.get("stock_price"), baseline.get("stock_price")),
        "industry_growth": finite(current.get("industry_growth_percent")),
    }
    scores = {key: _scalar_score(value) for key, value in changes.items()}
    weights = ((config.get("validation") or {}).get("outcome_weights") or {})
    outcome = weighted_available([(float(weights[key]), scores.get(key)) for key in weights])
    observed = sum(value is not None for value in scores.values())
    threshold = float((config.get("prediction") or {}).get("outcome_success_threshold", 60.0))
    return {
        "theme_id": baseline.get("theme_id"),
        "predicted_score": finite(baseline.get("predicted_score")),
        "predicted_rank": baseline.get("predicted_rank"),
        "realized_outcome_score": outcome,
        "realized_success": bool(outcome is not None and observed >= int((config.get("validation") or {}).get("minimum_observed_outcome_metrics", 2)) and outcome >= threshold),
        "observed_metric_count": observed,
        "changes": changes,
        "component_scores": scores,
        "baseline_as_of": baseline.get("as_of"),
        "evaluated_as_of": current.get("as_of"),
    }


def _classification_metrics(rows: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    usable = [row for row in rows if row.get("realized_outcome_score") is not None and row.get("predicted_score") is not None]
    predicted_positive = [bool(float(row["predicted_score"]) >= threshold) for row in usable]
    actual_positive = [bool(row.get("realized_success")) for row in usable]
    tp = sum(p and a for p, a in zip(predicted_positive, actual_positive))
    tn = sum((not p) and (not a) for p, a in zip(predicted_positive, actual_positive))
    fp = sum(p and (not a) for p, a in zip(predicted_positive, actual_positive))
    fn = sum((not p) and a for p, a in zip(predicted_positive, actual_positive))
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    specificity = tn / (tn + fp) if tn + fp else None
    false_negative_rate = fn / (fn + tp) if fn + tp else None
    hit_rate = (tp + tn) / len(usable) if usable else None
    ranked = sorted(usable, key=lambda row: (-float(row["predicted_score"]), str(row.get("theme_id"))))
    top_k = min(10, max(1, len(ranked) // 2)) if ranked else 0
    top = ranked[:top_k]
    bottom = ranked[-top_k:] if top_k else []
    top_success = sum(bool(row.get("realized_success")) for row in top) / len(top) if top else None
    top_avg = sum(float(row["realized_outcome_score"]) for row in top) / len(top) if top else None
    bottom_avg = sum(float(row["realized_outcome_score"]) for row in bottom) / len(bottom) if bottom else None
    correlation = pearson_correlation([float(row["predicted_score"]) for row in ranked], [float(row["realized_outcome_score"]) for row in ranked]) if ranked else None
    return {
        "theme_observation_count": len(usable),
        "threshold": threshold,
        "true_positive": tp,
        "true_negative": tn,
        "false_positive": fp,
        "false_negative": fn,
        "hit_rate": round(hit_rate, 6) if hit_rate is not None else None,
        "precision": round(precision, 6) if precision is not None else None,
        "recall": round(recall, 6) if recall is not None else None,
        "specificity": round(specificity, 6) if specificity is not None else None,
        "false_positive_rate": round(1.0 - specificity, 6) if specificity is not None else None,
        "false_negative_rate": round(false_negative_rate, 6) if false_negative_rate is not None else None,
        "top_10_success_rate": round(top_success, 6) if top_success is not None else None,
        "top_bottom_outcome_spread": round(top_avg - bottom_avg, 4) if top_avg is not None and bottom_avg is not None else None,
        "rank_correlation": correlation,
    }


def _load_registry(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schema_version": 1, "snapshots": []}
    payload = load_json(path)
    payload.setdefault("schema_version", 1)
    payload.setdefault("snapshots", [])
    return payload


def _save_immutable(root: Path, relative_dir: str, snapshot_id: str, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    base = root / relative_dir
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{snapshot_id}.json"
    if path.is_file():
        stored = load_json(path)
        if stored.get("content_sha256") == payload.get("content_sha256"):
            return "REUSED_IMMUTABLE_SNAPSHOT", stored
        suffix = str(payload.get("as_of") or "rerun")
        path = base / f"{snapshot_id}-{suffix}.json"
        if path.is_file():
            return "REUSED_IMMUTABLE_VERSIONED_SNAPSHOT", load_json(path)
        core = {key: value for key, value in payload.items() if key != "content_sha256"}
        core["snapshot_id"] = f"{snapshot_id}-{suffix}"
        payload = {**core, "content_sha256": canonical_sha256(core)}
    write_json(path, payload)
    return "CREATED_IMMUTABLE_SNAPSHOT", payload


def _frontier_discovery_documents(root: Path, as_of: date) -> list[dict[str, Any]]:
    """Reuse already-collected GitHub frontier text without any extra API calls."""
    payload = _load_optional(root, "data_cache/latest/v3_frontier_signal_observations.json")
    documents: list[dict[str, Any]] = []
    for row in payload.get("github") or []:
        theme_id = str(row.get("theme_id") or "")
        github = row.get("github") or {}
        for repo in github.get("repositories") or []:
            name = str(repo.get("full_name") or "").strip()
            description = str(repo.get("description") or "").strip()
            if not name and not description:
                continue
            captured = str(repo.get("pushed_at") or repo.get("updated_at") or repo.get("observation_date") or as_of.isoformat())[:10]
            documents.append({
                "document_id": "github:" + (name or canonical_sha256({"description": description})[:16]),
                "source": "github_frontier",
                "captured_at": captured,
                "title": name,
                "text": (name.replace("/", " ") + " " + description).strip(),
                "seed_theme_id": theme_id,
            })
    return documents


def _candidate_readiness(row: dict[str, Any], discovery: dict[str, Any]) -> dict[str, Any]:
    confidence = float(row.get("confidence") or 0.0)
    novelty = float(row.get("novelty_score") or 0.0) * 100.0
    documents = int(row.get("distinct_document_count") or 0)
    sources = int(row.get("source_family_count") or 0)
    periods = int(row.get("period_count") or 0)
    dates = int(row.get("distinct_date_count") or 0)
    date_span_days = int(row.get("date_span_days") or 0)
    evidence_score = min(
        100.0,
        documents * 6.0 + sources * 12.0 + periods * 4.0 + dates * 8.0 + min(12.0, date_span_days / 3.0),
    )
    score = round(0.55 * confidence + 0.25 * novelty + 0.20 * evidence_score, 2)
    missing = []
    if documents < int(discovery.get("min_documents", 2)):
        missing.append("문서 수")
    if sources < int(discovery.get("min_source_families", 2)):
        missing.append("서로 다른 원천")
    if periods < int(discovery.get("min_periods", 1)):
        missing.append("서로 다른 기간")
    if dates < int(discovery.get("min_distinct_dates", 1)):
        missing.append("서로 다른 날짜")
    if date_span_days < int(discovery.get("min_date_span_days", 0)):
        missing.append("관측 간격")
    if confidence < float(discovery.get("min_confidence", 70.0)):
        missing.append("신뢰도")
    if float(row.get("novelty_score") or 0.0) < float(discovery.get("min_novelty", 0.65)):
        missing.append("신규성")
    return {
        "readiness_score": score,
        "evidence_score": round(evidence_score, 2),
        "missing_requirements": missing,
        "review_priority": "HIGH" if score >= 80 else ("MEDIUM" if score >= 65 else "LOW"),
    }


def _false_positive_guard(item: dict[str, Any], quality: dict[str, Any], hidden: dict[str, Any]) -> dict[str, Any]:
    """Pre-maturity warning layer. It never changes the locked V7 score."""
    reasons: list[str] = []
    risk = 0.0
    q = float(quality.get("proxy_quality_score") or 0.0)
    observed = int(quality.get("observed_proxy_count") or 0)
    diffusion = finite(item.get("source_diffusion_percent"))
    if q < 50:
        risk += 35
        reasons.append("프록시 품질이 낮음")
    elif q < 65:
        risk += 18
        reasons.append("프록시 품질이 보통 이하")
    if observed < 3:
        risk += 15
        reasons.append("관측 프록시 수가 적음")
    if diffusion is None or diffusion < 40:
        risk += 20
        reasons.append("다원천 확인이 약함")
    if hidden.get("eligible") is not True:
        risk += 20
        reasons.append("저관심·실물·자금 교차조건 미충족")
    commercialization = finite(item.get("direct_commercialization_score"))
    if commercialization is not None and commercialization < 45:
        risk += 10
        reasons.append("사업화 신호가 약함")
    risk = round(min(100.0, risk), 2)
    return {
        "risk_score": risk,
        "risk_level": "HIGH" if risk >= 60 else ("MEDIUM" if risk >= 30 else "LOW"),
        "reasons": reasons,
        "locked_score_unchanged": True,
        "note": "장기 실측 검증 전 오탐 가능성을 낮추기 위한 경고 레이어이며 V7 점수는 변경하지 않습니다.",
    }



def _discovery_document_date(document: dict[str, Any], fallback: date) -> date:
    for key in ("captured_at", "published_at", "published", "as_of", "date"):
        value = document.get(key)
        if not value:
            continue
        parsed = _date_from(value, fallback)
        if parsed != fallback or str(value)[:10] == fallback.isoformat():
            return parsed
    return fallback


def _discovery_document_fingerprint(document: dict[str, Any]) -> str:
    text = str(document.get("text") or document.get("abstract") or document.get("summary") or document.get("title") or "")
    normalized = " ".join(text.lower().split())
    title = " ".join(str(document.get("title") or "").lower().split())
    return canonical_sha256({"title": title, "text": normalized})


def _rolling_discovery_corpus(
    root: Path,
    current_documents: list[dict[str, Any]],
    as_of: date,
    discovery: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Persist a bounded rolling discovery corpus without making any extra API calls."""
    history_path = root / str(discovery.get("rolling_corpus_path") or "prospective_history/v8_discovery_rolling_corpus.json")
    window_days = max(1, int(discovery.get("rolling_window_days", 90)))
    max_documents = max(10, int(discovery.get("max_rolling_documents", 600)))
    balance_min_size = max(0, int(discovery.get("source_balance_min_corpus_size", 40)))
    max_source_share = max(0.1, min(1.0, float(discovery.get("max_source_share", 0.50))))
    cutoff = as_of - timedelta(days=window_days - 1)

    previous_documents: list[dict[str, Any]] = []
    if history_path.is_file():
        try:
            previous_payload = load_json(history_path)
            previous_documents = list((previous_payload or {}).get("documents") or [])
        except (OSError, ValueError, TypeError):
            previous_documents = []

    merged: dict[str, dict[str, Any]] = {}
    duplicate_count = 0
    for origin, rows in (("history", previous_documents), ("current", current_documents)):
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("text") or raw.get("abstract") or raw.get("summary") or raw.get("title") or "").strip()
            if not text:
                continue
            document = dict(raw)
            observed = _discovery_document_date(document, as_of)
            if origin == "current" and not any(document.get(key) for key in ("captured_at", "published_at", "published", "as_of", "date")):
                observed = as_of
                document["captured_at"] = as_of.isoformat()
            elif not document.get("captured_at"):
                document["captured_at"] = observed.isoformat()
            fingerprint = _discovery_document_fingerprint(document)
            document["discovery_fingerprint"] = fingerprint
            document.setdefault("document_id", str(document.get("id") or fingerprint[:16]))
            existing = merged.get(fingerprint)
            if existing is None:
                merged[fingerprint] = document
                continue
            duplicate_count += 1
            existing_date = _discovery_document_date(existing, as_of)
            candidate_date = _discovery_document_date(document, as_of)
            richer = document if len(text) > len(str(existing.get("text") or existing.get("abstract") or existing.get("summary") or existing.get("title") or "")) else existing
            preserved = dict(richer)
            preserved["captured_at"] = min(existing_date, candidate_date).isoformat()
            preserved["discovery_fingerprint"] = fingerprint
            preserved.setdefault("document_id", str(existing.get("document_id") or document.get("document_id") or fingerprint[:16]))
            merged[fingerprint] = preserved

    in_window: list[dict[str, Any]] = []
    expired_count = 0
    for document in merged.values():
        observed = _discovery_document_date(document, as_of)
        if cutoff <= observed <= as_of:
            in_window.append(document)
        else:
            expired_count += 1

    in_window.sort(
        key=lambda row: (
            _discovery_document_date(row, as_of),
            len(str(row.get("text") or row.get("abstract") or row.get("summary") or row.get("title") or "")),
        ),
        reverse=True,
    )

    source_balance_dropped_count = 0
    if len(in_window) >= balance_min_size and max_source_share < 1.0:
        cap = max(1, int(math.ceil(len(in_window) * max_source_share)))
        source_counts: dict[str, int] = {}
        balanced: list[dict[str, Any]] = []
        for document in in_window:
            source = str(document.get("source") or "unknown")
            count = source_counts.get(source, 0)
            if count >= cap:
                source_balance_dropped_count += 1
                continue
            source_counts[source] = count + 1
            balanced.append(document)
        in_window = balanced

    max_document_dropped_count = max(0, len(in_window) - max_documents)
    if max_document_dropped_count:
        in_window = in_window[:max_documents]

    source_counts: dict[str, int] = {}
    for document in in_window:
        source = str(document.get("source") or "unknown")
        source_counts[source] = source_counts.get(source, 0) + 1

    payload = {
        "schema_version": 1,
        "as_of": as_of.isoformat(),
        "window_days": window_days,
        "cutoff_date": cutoff.isoformat(),
        "document_count": len(in_window),
        "source_counts": dict(sorted(source_counts.items())),
        "documents": in_window,
    }
    payload["content_sha256"] = canonical_sha256(payload)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(history_path, payload)

    stats = {
        "rolling_corpus_path": str(history_path.relative_to(root)),
        "rolling_window_days": window_days,
        "previous_document_count": len(previous_documents),
        "current_run_document_count": len(current_documents),
        "rolling_document_count": len(in_window),
        "deduplicated_document_count": duplicate_count,
        "expired_document_count": expired_count,
        "source_balance_dropped_count": source_balance_dropped_count,
        "max_document_dropped_count": max_document_dropped_count,
        "source_counts": dict(sorted(source_counts.items())),
    }
    return in_window, stats

def _discovery(root: Path, themes: list[dict[str, Any]], as_of: date, config: dict[str, Any]) -> dict[str, Any]:
    discovery = config.get("discovery") or {}
    input_path = root / str(discovery.get("input_path"))
    generated_input_path = root / str(discovery.get("generated_input_path") or "data_cache/latest/v3_dynamic_discovery_documents.json")
    documents: list[dict[str, Any]] = []
    input_sources: list[str] = []
    if input_path.is_file():
        payload = load_json(input_path)
        inbox_documents = payload if isinstance(payload, list) else list((payload or {}).get("documents") or [])
        if inbox_documents:
            documents.extend(inbox_documents)
            input_sources.append("manual_inbox")
    if generated_input_path.is_file():
        payload = load_json(generated_input_path)
        generated_documents = payload if isinstance(payload, list) else list((payload or {}).get("documents") or [])
        if generated_documents:
            documents.extend(generated_documents)
            input_sources.append("v3_dynamic_text")
    frontier_documents = _frontier_discovery_documents(root, as_of)
    if frontier_documents:
        documents.extend(frontier_documents)
        input_sources.append("github_frontier")
    documents, rolling_stats = _rolling_discovery_corpus(root, documents, as_of, discovery)
    if documents:
        # config/themes.yml uses id/name, while the dynamic-term helper expects
        # theme_id/theme_name. Normalize here so V8 discovery can consume the
        # canonical theme universe without changing the shared helper contract.
        discovery_themes = []
        for row in themes:
            if not isinstance(row, dict):
                continue
            theme_id = row.get("theme_id") or row.get("id")
            if not theme_id:
                continue
            normalized = dict(row)
            normalized["theme_id"] = str(theme_id)
            normalized["theme_name"] = row.get("theme_name") or row.get("name") or str(theme_id)
            if not normalized.get("openalex_search") and row.get("arxiv_query"):
                normalized["openalex_search"] = row.get("arxiv_query")
            discovery_themes.append(normalized)

        report = discover_candidates(
            documents,
            discovery_themes,
            as_of.isoformat(),
            min_documents=int(discovery.get("min_documents", 2)),
            min_source_families=int(discovery.get("min_source_families", 2)),
            min_periods=int(discovery.get("min_periods", 2)),
            min_similarity=float(discovery.get("min_similarity", 0.15)),
            max_candidates=int(discovery.get("max_candidates", 50)),
            min_phrase_tokens=int(discovery.get("min_phrase_tokens", 2)),
            max_phrase_tokens=int(discovery.get("max_phrase_tokens", 4)),
            min_phrase_quality=float(discovery.get("min_phrase_quality", 55.0)),
            existing_theme_similarity=float(discovery.get("existing_theme_similarity", 0.55)),
            min_distinct_dates=int(discovery.get("min_distinct_dates", 1)),
            min_date_span_days=int(discovery.get("min_date_span_days", 0)),
        )
    else:
        fallback = root / str(discovery.get("fallback_path"))
        report = load_json(fallback) if fallback.is_file() else {"status": "WAITING_FOR_DISCOVERY_CORPUS", "candidates": []}
    candidates = []
    for row in report.get("candidates") or []:
        similarity = float(row.get("semantic_similarity_proxy") or 0.0)
        novelty = round(1.0 - similarity, 4)
        phrase_quality = float(row.get("phrase_quality_score") or 0.0)
        existing_extension = str(row.get("promotion_status") or "") == "EXISTING_THEME_EXTENSION"
        challenger = (
            not existing_extension
            and novelty >= float(discovery.get("min_novelty", 0.65))
            and float(row.get("confidence") or 0.0) >= float(discovery.get("min_confidence", 70.0))
            and phrase_quality >= float(discovery.get("min_phrase_quality", 55.0))
            and int(row.get("distinct_document_count") or 0) >= int(discovery.get("min_challenger_documents", 3))
            and int(row.get("source_family_count") or 0) >= int(discovery.get("min_source_families", 2))
            and int(row.get("distinct_date_count") or 0) >= int(discovery.get("min_distinct_dates", 1))
            and int(row.get("date_span_days") or 0) >= int(discovery.get("min_date_span_days", 0))
        )
        enriched = {
            **row,
            "novelty_score": novelty,
            "candidate_class": "CHALLENGER_NEW_INDUSTRY" if challenger else ("EXISTING_THEME_EXTENSION" if existing_extension else "REVIEW_ONLY"),
            "promotion_status": "CHALLENGER_REVIEW_REQUIRED" if challenger else ("EXISTING_THEME_EXTENSION" if existing_extension else "REVIEW_REQUIRED"),
            "auto_add_allowed": False,
        }
        enriched.update(_candidate_readiness(enriched, discovery))
        candidates.append(enriched)
    candidates.sort(key=lambda row: (-float(row.get("readiness_score") or 0.0), -float(row.get("confidence") or 0.0), str(row.get("term") or "")))
    result = {
        "schema_version": 1,
        "engine_release": config.get("engine_release"),
        "as_of": as_of.isoformat(),
        "status": "CHALLENGERS_FOUND" if any(row["candidate_class"] == "CHALLENGER_NEW_INDUSTRY" for row in candidates) else report.get("status", "NO_QUALIFIED_CANDIDATES"),
        "input_document_count": len(documents),
        "input_source_families": sorted(set(input_sources)),
        "rolling_corpus": rolling_stats,
        "candidate_count": len(candidates),
        "challenger_count": sum(row["candidate_class"] == "CHALLENGER_NEW_INDUSTRY" for row in candidates),
        "auto_add_allowed": False,
        "promotion_rule": discovery,
        "candidates": candidates,
    }
    result["content_sha256"] = canonical_sha256(result)
    return result


def run_v8(root: Path, output_dir: Path, run_date: str | None = None, v70_output_dir: Path | None = None) -> dict[str, Any]:
    config = _load_optional(root, "config/v8_layers.json")
    if not config:
        raise V8Error("config/v8_layers.json is missing")
    v70_dir = v70_output_dir or root / "outputs/v70_final_engine"
    source_path = v70_dir / "v70_current_operational_snapshot.json"
    if not source_path.is_file():
        raise V8Error(f"V7 operational snapshot missing: {source_path}")
    source = load_json(source_path)
    as_of = date.fromisoformat(run_date or str(source.get("as_of")))
    v50 = _load_optional(root, "outputs/v50_final_validator/v50_current_run_snapshot.json")
    v50_map = _theme_map(v50)
    quality_rows = {row["theme_id"]: row for row in (proxy_quality_for_theme(root, str(item.get("theme_id")), as_of, config) for item in source.get("decisions") or [])}
    layer_themes: list[dict[str, Any]] = []
    for item in source.get("decisions") or []:
        theme_id = str(item.get("theme_id"))
        quality = quality_rows[theme_id]
        base_score = finite(item.get("boom_score"))
        adjusted = None if base_score is None else round(base_score * quality["quality_multiplier"], 4)
        hidden = _hidden_interaction(root, item, theme_id, config)
        false_positive_guard = _false_positive_guard(item, quality, hidden)
        v50_row = v50_map.get(theme_id) or {}
        layer_themes.append({
            "theme_id": theme_id,
            "theme_name": item.get("theme_name"),
            "sector": item.get("sector"),
            "as_of": as_of.isoformat(),
            "locked_base_score": base_score,
            "quality_adjusted_score": adjusted,
            "hidden_interaction": hidden,
            "false_positive_guard": false_positive_guard,
            "base_predicted_rank": item.get("rank"),
            "direct_commercialization_score": item.get("direct_commercialization_score") or v50_row.get("direct_commercialization_score"),
            "phase3_investment_score": item.get("phase3_investment_score") or v50_row.get("phase3_investment_score"),
            "proxy_quality": quality,
            "locked_score_unchanged": True,
        })
    layer = {"schema_version": 1, "engine_release": config["engine_release"], "as_of": as_of.isoformat(), "source_snapshot_id": source.get("snapshot_id"), "source_snapshot_sha256": source.get("content_sha256"), "theme_count": len(layer_themes), "investment_use_allowed": False, "themes": layer_themes}
    layer["content_sha256"] = canonical_sha256(layer)
    # V7 may itself create an as-of versioned snapshot on same-month refreshes.
    # V8 uses the calendar month as its own stable ledger key so reruns do not
    # compound version suffixes from the upstream snapshot id.
    action, stored_layer = _save_immutable(root, "prospective_history/v8_snapshots", as_of.strftime("%Y-%m"), layer)

    outcome = build_outcome_snapshot(root, as_of)
    outcome["content_sha256"] = canonical_sha256(outcome)
    outcome_path = root / "prospective_history/v8_outcome_snapshots" / f"{outcome['as_of']}.json"
    if not outcome_path.is_file():
        write_json(outcome_path, outcome)
    write_json(root / "data_cache/latest/v8_outcome_snapshot.json", outcome)

    registry_path = root / "prospective_history/v8_validation_registry.json"
    registry = _load_registry(registry_path)
    existing = next((row for row in registry["snapshots"] if row.get("snapshot_id") == stored_layer.get("snapshot_id")), None)
    if existing is None:
        existing = {"snapshot_id": stored_layer.get("snapshot_id"), "as_of": stored_layer.get("as_of"), "snapshot_sha256": stored_layer.get("content_sha256"), "evaluations": {}}
        registry["snapshots"].append(existing)
    all_outcomes = []
    for path in sorted((root / "prospective_history/v8_outcome_snapshots").glob("*.json")):
        try:
            all_outcomes.append(load_json(path))
        except (OSError, ValueError):
            continue
    horizon_results: dict[str, Any] = {}
    for horizon in config["evaluation_horizons_months"]:
        evaluations = []
        for registry_item in registry.get("snapshots") or []:
            baseline_path = root / "prospective_history/v8_snapshots" / f"{registry_item['snapshot_id']}.json"
            if not baseline_path.is_file():
                continue
            baseline = load_json(baseline_path)
            due = add_months(date.fromisoformat(str(baseline["as_of"])), int(horizon))
            candidates = [item for item in all_outcomes if _date_from(item.get("as_of"), date.min) >= due]
            if not candidates:
                continue
            current = sorted(candidates, key=lambda item: str(item.get("as_of")))[0]
            base_outcome = next((item for item in all_outcomes if str(item.get("as_of")) == str(baseline.get("as_of"))), None)
            if not base_outcome:
                continue
            base_map = _theme_map(base_outcome)
            current_map = _theme_map(current)
            rows = []
            for theme in baseline.get("themes") or []:
                theme_id = str(theme.get("theme_id"))
                if theme_id in base_map and theme_id in current_map:
                    baseline_row = dict(base_map[theme_id], theme_id=theme_id, as_of=base_outcome.get("as_of"))
                    current_row = dict(current_map[theme_id], theme_id=theme_id, as_of=current.get("as_of"))
                    prediction = dict(theme, predicted_score=theme.get("locked_base_score"), predicted_rank=theme.get("base_predicted_rank"), as_of=baseline.get("as_of"))
                    result = _evaluate_outcome({**baseline_row, **prediction}, current_row, config)
                    rows.append(result)
            metrics = _classification_metrics(rows, float((config.get("prediction") or {}).get("positive_threshold", 60.0)))
            evaluations.append({"snapshot_id": baseline.get("snapshot_id"), "baseline_as_of": baseline.get("as_of"), "evaluated_as_of": current.get("as_of"), "horizon_months": horizon, "metrics": metrics, "themes": rows})
        all_rows = [row for evaluation in evaluations for row in evaluation.get("themes") or []]
        horizon_results[str(horizon)] = {"matured_snapshot_count": len(evaluations), "evaluations": evaluations, "aggregate_metrics": _classification_metrics(all_rows, float((config.get("prediction") or {}).get("positive_threshold", 60.0)))}
    registry["content_sha256"] = canonical_sha256({"schema_version": registry.get("schema_version", 1), "snapshots": registry.get("snapshots", [])})
    write_json(registry_path, registry)

    scorecard = {"schema_version": 1, "engine_release": config["engine_release"], "status": "PROSPECTIVE_VALIDATION_ACTIVE" if any(row["matured_snapshot_count"] for row in horizon_results.values()) else "PROSPECTIVE_VALIDATION_ACCUMULATING", "horizons": horizon_results, "investment_use_allowed": False, "gate_checks": []}
    for horizon, result in horizon_results.items():
        required = int((config.get("validation") or {}).get("minimum_matured_snapshots", {}).get(horizon, 1))
        scorecard["gate_checks"].append({"horizon_months": int(horizon), "matured_snapshot_count": result["matured_snapshot_count"], "required": required, "passed": result["matured_snapshot_count"] >= required})
    # Compare discovery terms against the complete current 50-theme V7 universe, not the smaller collector config.
    discovery = _discovery(root, source.get("decisions") or [], as_of, config)
    dashboard = {"status": scorecard["status"], "as_of": as_of.isoformat(), "theme_count": len(layer_themes), "proxy_quality": {"mean_quality_score": round(sum(row["proxy_quality"]["proxy_quality_score"] for row in layer_themes) / max(1, len(layer_themes)), 4)}, "validation": scorecard, "discovery": discovery, "investment_use_allowed": False}
    summary = {"status": "V8_VALIDATION_QUALITY_DISCOVERY_ACTIVE", "engine_release": config["engine_release"], "as_of": as_of.isoformat(), "theme_count": len(layer_themes), "snapshot_action": action, "proxy_quality_observed_theme_count": sum(row["proxy_quality"]["observed_proxy_count"] > 0 for row in layer_themes), "mean_proxy_quality_score": dashboard["proxy_quality"]["mean_quality_score"], "validation_status": scorecard["status"], "matured_snapshot_counts": {key: value["matured_snapshot_count"] for key, value in horizon_results.items()}, "discovery_challenger_count": discovery["challenger_count"], "locked_score_mutated": False, "investment_use_allowed": False}
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in (("v8_run_summary.json", summary), ("v8_current_layer_snapshot.json", stored_layer), ("v8_proxy_quality_report.json", {"as_of": as_of.isoformat(), "themes": layer_themes}), ("v8_validation_scorecard.json", scorecard), ("v8_discovery_challengers.json", discovery), ("v8_outcome_snapshot.json", outcome), ("v8_dashboard_payload.json", dashboard), ("v8_next_gate.json", {"current_status": summary["status"], "next_required_gate": "ACCUMULATE_MATURE_6_12_24_MONTH_OUTCOMES_AND_REVIEW_CHALLENGERS", "investment_use_allowed": False})):
        write_json(output_dir / name, payload)
    return summary
