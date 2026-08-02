from __future__ import annotations

import hashlib
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ible.v3_http import HttpError, JsonHttpClient
from ible.v32_xlsx import read_xlsx_rows


_MISSING = {"", "D", "NA", "N/A", "Z", "S", "X", "—", "-"}


@dataclass(frozen=True)
class OfficialWorkbook:
    payload: bytes
    status: str
    sha256: str
    source_error: str | None


def load_official_workbook(
    client: JsonHttpClient,
    url: str,
    bundled_path: Path,
) -> OfficialWorkbook:
    source_error: str | None = None
    if os.environ.get("IBLE_OFFLINE") == "1":
        source_error = "IBLE_OFFLINE=1: live download skipped"
    else:
        try:
            payload = client.request_bytes(
                url,
                accept="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,*/*",
            )
            if not payload.startswith(b"PK"):
                raise HttpError("downloaded payload is not an xlsx workbook")
            return OfficialWorkbook(payload, "LIVE_COLLECTED", hashlib.sha256(payload).hexdigest(), None)
        except (HttpError, OSError, ValueError) as exc:
            source_error = str(exc)[:1200]
    if not bundled_path.is_file():
        raise HttpError(f"live download failed and bundled official seed is missing: {source_error}")
    payload = bundled_path.read_bytes()
    if not payload.startswith(b"PK"):
        raise HttpError("bundled official seed is not an xlsx workbook")
    return OfficialWorkbook(
        payload,
        "BUNDLED_OFFICIAL_SEED",
        hashlib.sha256(payload).hexdigest(),
        source_error,
    )


def numeric_value(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    text = str(value).strip().replace("\xa0", " ")
    if text.upper() in _MISSING:
        return None
    text = re.sub(r"\([^)]*\)", "", text).strip()
    text = re.sub(r"\s+[a-zA-Z]+\s*$", "", text).strip()
    range_match = re.fullmatch(r"([+-]?[\d,]+(?:\.\d+)?)\s*[-–]\s*([+-]?[\d,]+(?:\.\d+)?)", text)
    if range_match:
        left = float(range_match.group(1).replace(",", ""))
        right = float(range_match.group(2).replace(",", ""))
        return (left + right) / 2.0
    cleaned = text.replace(",", "").replace("$", "").strip()
    match = re.search(r"[+-]?\d+(?:\.\d+)?", cleaned)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def normalize_naics_tokens(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [str(int(value))]
    text = str(value).strip().lower()
    if text.startswith("other "):
        return []
    text = re.sub(r"\([^)]*\)", "", text)
    text = text.replace("–", "-").replace("—", "-")
    tokens: list[str] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if match:
            left, right = match.groups()
            if len(right) < len(left):
                right = left[: len(left) - len(right)] + right
            if len(left) == len(right):
                start, end = int(left), int(right)
                if 0 <= end - start <= 30:
                    tokens.extend(str(number) for number in range(start, end + 1))
                    continue
        digits = re.sub(r"\D", "", part)
        if digits:
            tokens.append(digits)
    return list(dict.fromkeys(tokens))


def _find_header(rows: list[list[Any]], required: str) -> int:
    for index, row in enumerate(rows):
        if any(str(value).strip() == required for value in row if value is not None):
            return index
    raise ValueError(f"required xlsx header not found: {required}")


def parse_aies_capex(payload: bytes) -> list[dict[str, Any]]:
    rows = read_xlsx_rows(payload, "Data")
    header_index = _find_header(rows, "2017 NAICS code")
    header = [str(value).strip() if value is not None else "" for value in rows[header_index]]
    index = {name: pos for pos, name in enumerate(header)}
    output: list[dict[str, Any]] = []
    for row_number, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
        def cell(name: str) -> Any:
            pos = index[name]
            return row[pos] if pos < len(row) else None
        if str(cell("Geographic Area Name") or "").strip() != "United States":
            continue
        codes = normalize_naics_tokens(cell("2017 NAICS code"))
        total = numeric_value(cell("Total capital expenditures ($1,000)"))
        if not codes or total is None:
            continue
        output.append({
            "row_number": row_number,
            "codes": codes,
            "naics_raw": cell("2017 NAICS code"),
            "industry": cell("Meaning of NAICS code"),
            "total_capex_thousand_usd": total,
            "structures_capex_thousand_usd": numeric_value(cell("Capital expenditures for buildings and other structures ($1,000)")),
            "equipment_capex_thousand_usd": numeric_value(cell("Capital expenditures for machinery and equipment ($1,000)")),
            "coefficient_of_variation_pct": numeric_value(cell("Coefficient of variation for total capital expenditures (%)")),
        })
    return output


def parse_berd_rd(payload: bytes, prior_year: int = 2022, current_year: int = 2023) -> list[dict[str, Any]]:
    rows = read_xlsx_rows(payload, "Table 58")
    if len(rows) < 5:
        raise ValueError("BERD workbook is too short")
    year_row = rows[3]
    year_columns: dict[int, int] = {}
    for index, value in enumerate(year_row):
        parsed = numeric_value(value)
        if parsed is not None and 1900 <= int(parsed) <= 2200:
            year_columns[int(parsed)] = index
    if prior_year not in year_columns or current_year not in year_columns:
        raise ValueError("BERD prior/current year columns missing")
    output: list[dict[str, Any]] = []
    for row_number, row in enumerate(rows[4:], start=5):
        if len(row) < 2:
            continue
        codes = normalize_naics_tokens(row[1])
        if not codes:
            continue
        prior = numeric_value(row[year_columns[prior_year]] if year_columns[prior_year] < len(row) else None)
        current = numeric_value(row[year_columns[current_year]] if year_columns[current_year] < len(row) else None)
        if prior is None and current is None:
            continue
        output.append({
            "row_number": row_number,
            "codes": codes,
            "naics_raw": row[1],
            "industry": row[0],
            f"rd_million_usd_{prior_year}": prior,
            f"rd_million_usd_{current_year}": current,
        })
    return output


def select_naics_rows(source_rows: list[dict[str, Any]], target_codes: list[str]) -> list[dict[str, Any]]:
    selected: dict[int, dict[str, Any]] = {}
    for raw_target in target_codes:
        target = re.sub(r"\D", "", str(raw_target))
        if not target:
            continue
        parent_candidates: list[tuple[int, dict[str, Any]]] = []
        child_candidates: list[tuple[int, dict[str, Any]]] = []
        for row in source_rows:
            for code in row.get("codes") or []:
                if target.startswith(code):
                    parent_candidates.append((len(code), row))
                elif code.startswith(target):
                    child_candidates.append((len(code), row))
        if parent_candidates:
            best_length = max(length for length, _ in parent_candidates)
            candidates = [row for length, row in parent_candidates if length == best_length]
        elif child_candidates:
            shortest = min(length for length, _ in child_candidates)
            candidates = [row for length, row in child_candidates if length == shortest]
        else:
            candidates = []
        for row in candidates:
            selected[int(row["row_number"])] = row
    return list(selected.values())


def percentile_scores(values: dict[str, float | None]) -> dict[str, float | None]:
    observed = sorted(value for value in values.values() if value is not None and math.isfinite(value))
    output: dict[str, float | None] = {}
    for key, value in values.items():
        if value is None or not math.isfinite(value) or not observed:
            output[key] = None
            continue
        less = sum(1 for candidate in observed if candidate < value)
        equal = sum(1 for candidate in observed if candidate == value)
        percentile = 100.0 * (less + 0.5 * equal) / len(observed)
        output[key] = round(percentile, 4)
    return output


def bounded_growth_score(growth_ratio: float | None) -> float | None:
    if growth_ratio is None or not math.isfinite(growth_ratio):
        return None
    return round(max(0.0, min(100.0, 50.0 + 50.0 * math.tanh(growth_ratio / 0.35))), 4)


def aggregate_capex(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"matched_rows": 0, "total_capex_thousand_usd": None, "coefficient_of_variation_pct": None}
    total = sum(float(row["total_capex_thousand_usd"]) for row in rows)
    weighted_cv_pairs = [
        (float(row["total_capex_thousand_usd"]), row.get("coefficient_of_variation_pct"))
        for row in rows if row.get("coefficient_of_variation_pct") is not None
    ]
    if weighted_cv_pairs and sum(value for value, _ in weighted_cv_pairs) > 0:
        cv = sum(value * float(item_cv) for value, item_cv in weighted_cv_pairs) / sum(value for value, _ in weighted_cv_pairs)
    else:
        cv = None
    return {
        "matched_rows": len(rows),
        "matched_naics": [row["naics_raw"] for row in rows],
        "total_capex_thousand_usd": round(total, 4),
        "coefficient_of_variation_pct": None if cv is None else round(cv, 4),
    }


def aggregate_rd(rows: list[dict[str, Any]], prior_year: int, current_year: int) -> dict[str, Any]:
    prior_key = f"rd_million_usd_{prior_year}"
    current_key = f"rd_million_usd_{current_year}"
    prior_values = [float(row[prior_key]) for row in rows if row.get(prior_key) is not None]
    current_values = [float(row[current_key]) for row in rows if row.get(current_key) is not None]
    prior = sum(prior_values) if prior_values else None
    current = sum(current_values) if current_values else None
    growth = None
    if prior is not None and current is not None and prior > 0:
        growth = current / prior - 1.0
    return {
        "matched_rows": len(rows),
        "matched_naics": [row["naics_raw"] for row in rows],
        prior_key: None if prior is None else round(prior, 4),
        current_key: None if current is None else round(current, 4),
        "rd_growth_ratio": None if growth is None else round(growth, 6),
    }
