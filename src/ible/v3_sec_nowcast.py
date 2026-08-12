from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any

from ible.analytics.sec_metrics import FLOW_TAGS, find_fact, quarterly_flow
from ible.config import load_yaml
from ible.integrity import canonical_sha256, load_json, write_json


_POSITIVE_MDNA = {
    "capacity expansion", "capital expenditure", "capital expenditures", "capex",
    "increase investment", "increased investment", "capacity investment",
    "expansion plans", "construction in progress", "under construction",
}
_NEGATIVE_MDNA = {
    "reduce capital expenditure", "reduced capital expenditure", "capex reduction",
    "defer investment", "deferred investment", "capacity reduction", "restructuring",
}


def _filing_date(row: dict[str, Any]) -> str:
    for key in ("accepted_at", "filing_date", "filed_at", "as_of", "period_end"):
        value = str(row.get(key) or "")[:10]
        try:
            date.fromisoformat(value)
            return value
        except ValueError:
            continue
    return ""


def _text(row: dict[str, Any]) -> str:
    values = [row.get("mdna_text"), row.get("mda_text"), row.get("mdna"), row.get("text"), row.get("summary")]
    return " ".join(str(value) for value in values if value).lower()


def _signal(row: dict[str, Any]) -> tuple[float | None, dict[str, Any]]:
    text = _text(row)
    positive = sum(text.count(term) for term in _POSITIVE_MDNA)
    negative = sum(text.count(term) for term in _NEGATIVE_MDNA)
    capex = row.get("capex")
    prior_capex = row.get("prior_capex")
    capex_growth = None
    try:
        if capex is not None and prior_capex not in (None, 0):
            capex_growth = (float(capex) - float(prior_capex)) / abs(float(prior_capex))
    except (TypeError, ValueError, ZeroDivisionError):
        capex_growth = None
    if positive == 0 and negative == 0 and capex_growth is None:
        return None, {"positive_mdna_term_count": 0, "negative_mdna_term_count": 0, "capex_growth_ratio": None}
    score = 50.0 + 12.0 * min(3, positive) - 15.0 * min(3, negative)
    if capex_growth is not None:
        score += max(-20.0, min(20.0, 100.0 * capex_growth))
    return max(0.0, min(100.0, score)), {
        "positive_mdna_term_count": positive,
        "negative_mdna_term_count": negative,
        "capex_growth_ratio": round(capex_growth, 6) if capex_growth is not None else None,
    }


def _load_filings(root: Path) -> tuple[list[dict[str, Any]], str | None]:
    paths = [
        root / "data_cache/latest/sec_mdna_capex_observations.json",
        root / "data_cache/latest/sec_filing_observations.json",
    ]
    for path in paths:
        if not path.is_file():
            continue
        try:
            payload = load_json(path)
        except Exception:
            continue
        rows = payload if isinstance(payload, list) else list((payload or {}).get("filings") or (payload or {}).get("observations") or [])
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)], str(path.relative_to(root))
    return [], None


def _selected_tickers(root: Path, limit: int = 20) -> tuple[dict[str, list[str]], list[str]]:
    payload = load_yaml(root / "config/theme_exposures.yml")
    by_theme: dict[str, list[str]] = {}
    scores: dict[str, float] = {}
    for theme in payload.get("themes") or []:
        theme_id = str(theme.get("id") or "")
        tickers = []
        for company in theme.get("us_companies") or []:
            ticker = str(company.get("ticker") or "").upper()
            exposure = float(company.get("exposure") or 0)
            if ticker and exposure >= float(payload.get("minimum_exposure") or 0.30):
                tickers.append(ticker)
                scores[ticker] = max(scores.get(ticker, 0.0), exposure)
        if theme_id:
            by_theme[theme_id] = tickers
    selected = [ticker for ticker, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:max(0, limit)]]
    return by_theme, selected


def ingest_sec_companyfacts(root: Path, requested_as_of: str, max_tickers: int = 20) -> dict[str, Any]:
    """Build the local SEC filing inbox from cached/limited Company Facts data.

    Company Facts provides filing facts, not MD&A prose. This stage therefore emits
    CAPEX-only evidence and records MD&A as unavailable instead of inferring it.
    """
    user_agent = os.getenv("SEC_USER_AGENT", "").strip()
    if not user_agent:
        return {"status": "WAITING_FOR_SEC_USER_AGENT", "external_api_calls": 0, "company_count": 0, "filing_count": 0}
    try:
        from ible.collectors.sec_bulk import SecBulkClient, SecBulkError
    except ImportError as exc:
        return {"status": "SEC_INGEST_DEPENDENCY_MISSING", "external_api_calls": 0, "company_count": 0, "filing_count": 0, "error": str(exc)}
    by_theme, tickers = _selected_tickers(root, max_tickers)
    client = SecBulkClient(root / ".cache/sec_bulk", user_agent, timeout=120, min_interval=0.35)
    try:
        preparation = client.prepare_subset(json_safe_map(root), tickers, source_mode=os.getenv("SEC_SOURCE_MODE", "auto"))
        facts, errors = client.load_subset(tickers)
    except (SecBulkError, OSError, ValueError) as exc:
        return {"status": "SEC_INGEST_FAILED_CACHE_PRESERVED", "external_api_calls": 0, "company_count": 0, "filing_count": 0, "error": str(exc)[:500]}
    filings: list[dict[str, Any]] = []
    for theme_id, theme_tickers in by_theme.items():
        for ticker in theme_tickers:
            if ticker not in facts:
                continue
            _, series = quarterly_flow(facts[ticker], FLOW_TAGS["capex"], requested_as_of)
            if not series:
                continue
            latest_end, latest_value = series[-1]
            prior_value = series[-2][1] if len(series) > 1 else None
            tag, fact = find_fact(facts[ticker], FLOW_TAGS["capex"])
            filed_dates = []
            if fact:
                for unit_values in (fact.get("units") or {}).values():
                    for item in unit_values or []:
                        filed = str(item.get("filed") or "")[:10]
                        if filed and filed <= str(requested_as_of)[:10]:
                            filed_dates.append(filed)
            filings.append({
                "theme_id": theme_id,
                "ticker": ticker,
                "filing_date": max(filed_dates) if filed_dates else latest_end,
                "period_end": latest_end,
                "capex": latest_value,
                "prior_capex": prior_value,
                "mdna_status": "NOT_AVAILABLE_FROM_COMPANYFACTS",
                "mdna_text": "",
                "fact_tag": tag,
            })
    input_path = root / "data_cache/latest/sec_mdna_capex_observations.json"
    write_json(input_path, {"schema_version": 1, "as_of": requested_as_of, "source": "SEC_COMPANYFACTS_CACHE", "filings": filings})
    external_calls = int(preparation.get("api_downloaded") or 0) + (1 if preparation.get("bulk_extracted") else 0)
    return {"status": "SEC_COMPANYFACTS_CAPEX_INGESTED" if filings else "SEC_COMPANYFACTS_NO_CAPEX_FACTS", "external_api_calls": external_calls, "company_count": len(facts), "filing_count": len(filings), "errors": errors, "preparation": preparation}


def json_safe_map(root: Path) -> dict[str, str]:
    path = root / "config/sec_cik_map.json"
    payload = load_json(path)
    return {str(key).upper(): str(value) for key, value in payload.items()}


def build_sec_nowcast(root: Path, themes: list[dict[str, Any]], requested_as_of: str) -> dict[str, Any]:
    filings, input_path = _load_filings(root)
    theme_ids = {str(row.get("theme_id")) for row in themes}
    rows = []
    future_rejected = 0
    for theme_id in sorted(theme_ids):
        usable = []
        for filing in filings:
            filing_themes = {str(value) for value in (filing.get("theme_ids") or filing.get("themes") or ([filing.get("theme_id")] if filing.get("theme_id") else []))}
            if theme_id not in filing_themes:
                continue
            filed = _filing_date(filing)
            if not filed or filed > str(requested_as_of)[:10]:
                if filed > str(requested_as_of)[:10]:
                    future_rejected += 1
                continue
            score, details = _signal(filing)
            if score is not None:
                usable.append((filed, score, details))
        usable.sort(key=lambda item: item[0])
        latest = usable[-1] if usable else None
        rows.append({
            "theme_id": theme_id,
            "status": "SEC_MDNA_CAPEX_OBSERVED" if latest else "NO_SEC_FILING_CACHE",
            "filing_date": latest[0] if latest else None,
            "sec_nowcast_score": latest[1] if latest else None,
            "evidence": latest[2] if latest else {},
            "filing_count": len(usable),
            "lookahead_guard": "PASSED",
            "investment_use_allowed": False,
        })
    observed = sum(row["status"] == "SEC_MDNA_CAPEX_OBSERVED" for row in rows)
    result = {
        "schema_version": 1,
        "as_of": str(requested_as_of),
        "status": "SEC_MDNA_CAPEX_OBSERVED" if observed else "WAITING_FOR_SEC_FILING_CACHE",
        "theme_count": len(rows),
        "observed_theme_count": observed,
        "future_filing_rejected_count": future_rejected,
        "input_path": input_path,
        "external_api_calls": 0,
        "investment_use_allowed": False,
        "themes": rows,
    }
    result["content_sha256"] = canonical_sha256(result)
    return result


def write_sec_nowcast(root: Path, output_dir: Path, result: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "v3_sec_mdna_capex_nowcast.json", result)
    write_json(root / "data_cache/latest/v3_sec_mdna_capex_nowcast.json", result)
