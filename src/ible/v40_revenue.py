from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from ible.v3_http import JsonHttpClient
from ible.v32_investment import (
    OfficialWorkbook,
    bounded_growth_score,
    load_official_workbook,
    numeric_value,
    percentile_scores,
    select_naics_rows,
)
from ible.v32_xlsx import list_xlsx_sheets, read_xlsx_rows


def _clean_label(value: Any) -> str:
    text = str(value or "").replace("\xa0", " ")
    text = re.sub(r"[.·…]+\s*$", "", text)
    return " ".join(text.split())


def parse_qss_revenue(payload: bytes) -> list[dict[str, Any]]:
    names = [name for name in list_xlsx_sheets(payload) if name.lower().startswith("table1a-")]
    if not names:
        raise ValueError("QSS table1a sheet missing")
    sheet = sorted(names)[-1]
    rows = read_xlsx_rows(payload, sheet)
    output: list[dict[str, Any]] = []
    for row_number, row in enumerate(rows[5:], start=6):
        code_value = row[0] if len(row) > 0 else None
        label = _clean_label(row[1] if len(row) > 1 else None)
        current = numeric_value(row[2] if len(row) > 2 else None)
        prior_year = numeric_value(row[6] if len(row) > 6 else None)
        yoy = numeric_value(row[12] if len(row) > 12 else None)
        digits = re.sub(r"\D", "", str(code_value or ""))
        if not digits or current is None:
            continue
        if yoy is None and prior_year not in (None, 0):
            yoy = 100.0 * (current / float(prior_year) - 1.0)
        output.append({
            "row_number": row_number,
            "codes": [digits],
            "naics": digits,
            "industry": label,
            "current_revenue_million_usd": current,
            "prior_year_revenue_million_usd": prior_year,
            "revenue_yoy_percent": yoy,
            "sheet": sheet,
        })
    return output


def aggregate_qss(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"matched_rows": 0, "current_revenue_million_usd": None, "revenue_yoy_percent": None}
    current = sum(float(row["current_revenue_million_usd"]) for row in rows if row.get("current_revenue_million_usd") is not None)
    prior_values = [float(row["prior_year_revenue_million_usd"]) for row in rows if row.get("prior_year_revenue_million_usd") is not None]
    prior = sum(prior_values) if prior_values else None
    yoy = None
    if prior not in (None, 0):
        yoy = 100.0 * (current / prior - 1.0)
    return {
        "matched_rows": len(rows),
        "matched_naics": [row["naics"] for row in rows],
        "matched_industries": [row["industry"] for row in rows],
        "current_revenue_million_usd": round(current, 4),
        "prior_year_revenue_million_usd": None if prior is None else round(prior, 4),
        "revenue_yoy_percent": None if yoy is None else round(yoy, 4),
    }


def parse_m3_series(payload: bytes) -> dict[str, dict[tuple[int, int], float]]:
    names = list_xlsx_sheets(payload)
    if not names:
        raise ValueError("M3 workbook has no sheet")
    rows = read_xlsx_rows(payload, names[0])
    series: dict[str, dict[tuple[int, int], float]] = {}
    for row in rows:
        if len(row) < 3 or row[0] is None or row[1] is None:
            continue
        series_id = str(row[0]).strip().upper()
        year_value = numeric_value(row[1])
        if year_value is None:
            continue
        year = int(year_value)
        target = series.setdefault(series_id, {})
        for month in range(1, 13):
            position = month + 1
            value = numeric_value(row[position] if position < len(row) else None)
            if value is not None:
                target[(year, month)] = value
    return series


def latest_yoy(series: dict[tuple[int, int], float]) -> dict[str, Any]:
    if not series:
        return {"latest_period": None, "latest_value_million_usd": None, "yoy_percent": None}
    periods = sorted(series)
    latest = periods[-1]
    latest_value = float(series[latest])
    prior = (latest[0] - 1, latest[1])
    prior_value = series.get(prior)
    yoy = None if prior_value in (None, 0) else 100.0 * (latest_value / float(prior_value) - 1.0)
    return {
        "latest_period": f"{latest[0]:04d}-{latest[1]:02d}",
        "latest_value_million_usd": round(latest_value, 4),
        "prior_year_value_million_usd": None if prior_value is None else round(float(prior_value), 4),
        "yoy_percent": None if yoy is None else round(yoy, 4),
    }


def m3_codes_for_naics(target_codes: list[str], proxy_map: dict[str, str]) -> list[str]:
    matched: list[str] = []
    ordered = sorted(proxy_map.items(), key=lambda item: len(item[0]), reverse=True)
    for raw in target_codes:
        target = re.sub(r"\D", "", str(raw))
        for prefix, code in ordered:
            if target.startswith(prefix) or prefix.startswith(target):
                matched.append(code)
                break
    return list(dict.fromkeys(matched))


def aggregate_m3(
    series: dict[str, dict[tuple[int, int], float]],
    codes: list[str],
    suffix: str,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for code in codes:
        item = latest_yoy(series.get(f"A{code}{suffix}", {}))
        if item["latest_value_million_usd"] is not None:
            rows.append({"m3_code": code, **item})
    if not rows:
        return {"matched_series": 0, "latest_value_million_usd": None, "yoy_percent": None}
    latest_period = max(str(row["latest_period"]) for row in rows)
    current = sum(float(row["latest_value_million_usd"]) for row in rows if row["latest_period"] == latest_period)
    prior_values = [float(row["prior_year_value_million_usd"]) for row in rows if row["latest_period"] == latest_period and row.get("prior_year_value_million_usd") is not None]
    prior = sum(prior_values) if prior_values else None
    yoy = None if prior in (None, 0) else 100.0 * (current / prior - 1.0)
    return {
        "matched_series": len(rows),
        "matched_m3_codes": [row["m3_code"] for row in rows],
        "latest_period": latest_period,
        "latest_value_million_usd": round(current, 4),
        "prior_year_value_million_usd": None if prior is None else round(prior, 4),
        "yoy_percent": None if yoy is None else round(yoy, 4),
    }


def load_v40_workbooks(root: Path, client: JsonHttpClient, config: dict[str, Any]) -> dict[str, OfficialWorkbook]:
    output: dict[str, OfficialWorkbook] = {}
    for key, source in config["sources"].items():
        output[key] = load_official_workbook(client, str(source["url"]), root / str(source["bundled_path"]))
    return output


def score_growth(percent: float | None) -> float | None:
    return None if percent is None else bounded_growth_score(float(percent) / 100.0)


def source_specific_percentiles(values: dict[str, tuple[str, float | None]]) -> dict[str, float | None]:
    by_family: dict[str, dict[str, float | None]] = {}
    for theme_id, (family, value) in values.items():
        by_family.setdefault(family, {})[theme_id] = value
    family_scores = {family: percentile_scores(items) for family, items in by_family.items()}
    return {theme_id: family_scores[family].get(theme_id) for theme_id, (family, _) in values.items()}
