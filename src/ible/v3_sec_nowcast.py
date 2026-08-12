from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

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
