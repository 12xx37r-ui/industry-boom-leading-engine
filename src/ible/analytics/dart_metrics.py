from __future__ import annotations

import datetime as dt
import math
import re
from collections import defaultdict
from typing import Any

REPORTS = (
    ("11013", 5, 15, 1),   # Q1, normally available by mid-May
    ("11012", 8, 14, 2),   # half-year
    ("11014", 11, 14, 3),  # Q3
    ("11011", 3, 31, 4),   # annual report in following year
)

REVENUE_NAMES = {
    "매출액",
    "수익(매출액)",
    "영업수익",
    "매출",
    "총수익",
}
OPERATING_PROFIT_NAMES = {
    "영업이익",
    "영업이익(손실)",
    "영업손익",
}

_UNIT_SCALE = {
    "원": 1.0,
    "천원": 1_000.0,
    "백만원": 1_000_000.0,
    "억원": 100_000_000.0,
    "조원": 1_000_000_000_000.0,
}
_AMOUNT_NUMBER = r"([0-9][0-9,]*(?:\.[0-9]+)?)"
_AMOUNT_UNIT = r"(조원|억원|백만원|천원|원)?"
_AMOUNT_LABELS = {
    "CAPITAL_EVENT": (
        "신규시설투자금액",
        "시설투자금액",
        "투자금액",
        "취득금액",
        "취득예정금액",
        "투자예정금액",
    ),
    "CONTRACT_EVENT": (
        "판매ㆍ공급계약금액",
        "판매·공급계약금액",
        "공급계약금액",
        "계약금액",
        "수주금액",
    ),
}


def parse_amount(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--"}:
        return None
    # DART sometimes uses parentheses for negative values.
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    try:
        parsed = float(text)
    except ValueError:
        return None
    return -parsed if negative else parsed


def report_available(as_of: dt.date, business_year: int, report_code: str) -> bool:
    if report_code == "11011":
        available = dt.date(business_year + 1, 3, 31)
    else:
        _, month, day, _ = next(row for row in REPORTS if row[0] == report_code)
        available = dt.date(business_year, month, day)
    return available <= as_of


def report_quarter(report_code: str) -> int:
    return next(row[3] for row in REPORTS if row[0] == report_code)


def _preferred_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cfs = [row for row in rows if row.get("fs_div") == "CFS"]
    return cfs or [row for row in rows if row.get("fs_div") == "OFS"] or rows


def build_quarterly_financial_series(
    rows_by_report: dict[tuple[int, str], list[dict[str, Any]]],
    stock_codes: list[str],
) -> tuple[dict[str, list[tuple[str, float]]], dict[str, list[tuple[str, float]]]]:
    cumulative: dict[str, dict[str, dict[tuple[int, int], float]]] = {
        "revenue": defaultdict(dict),
        "operating_profit": defaultdict(dict),
    }
    for (year, report_code), rows in rows_by_report.items():
        quarter = report_quarter(report_code)
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            stock_code = str(row.get("stock_code") or "").strip()
            account_name = str(row.get("account_nm") or "").strip()
            if stock_code:
                grouped[(stock_code, account_name)].append(row)
        for (stock_code, account_name), group in grouped.items():
            metric = None
            if account_name in REVENUE_NAMES:
                metric = "revenue"
            elif account_name in OPERATING_PROFIT_NAMES:
                metric = "operating_profit"
            if not metric:
                continue
            row = _preferred_rows(group)[0]
            amount = parse_amount(row.get("thstrm_add_amount"))
            if amount is None:
                amount = parse_amount(row.get("thstrm_amount"))
            if amount is not None:
                cumulative[metric][stock_code][(year, quarter)] = amount

    output: dict[str, dict[str, list[tuple[str, float]]]] = {
        "revenue": {code: [] for code in stock_codes},
        "operating_profit": {code: [] for code in stock_codes},
    }
    quarter_ends = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}
    for metric in ("revenue", "operating_profit"):
        for stock_code in stock_codes:
            values = cumulative[metric].get(stock_code, {})
            for year in sorted({key[0] for key in values}):
                prior_cumulative = 0.0
                for quarter in (1, 2, 3, 4):
                    current = values.get((year, quarter))
                    if current is None:
                        continue
                    quarter_value = current if quarter == 1 else current - prior_cumulative
                    prior_cumulative = current
                    output[metric][stock_code].append((f"{year}-{quarter_ends[quarter]}", quarter_value))
    return output["revenue"], output["operating_profit"]


def classify_disclosure(report_name: str) -> str | None:
    name = report_name.replace(" ", "")
    capital_terms = (
        "신규시설투자",
        "시설투자",
        "타법인주식및출자증권취득결정",
        "유형자산취득결정",
    )
    contract_terms = (
        "단일판매ㆍ공급계약체결",
        "단일판매·공급계약체결",
        "단일판매공급계약체결",
        "공급계약",
        "수주계약",
    )
    if any(term.replace(" ", "") in name for term in capital_terms):
        return "CAPITAL_EVENT"
    if any(term.replace(" ", "") in name for term in contract_terms):
        return "CONTRACT_EVENT"
    return None


def _infer_unit_scale(context: str, explicit_unit: str | None) -> tuple[float, str, float]:
    if explicit_unit:
        return _UNIT_SCALE[explicit_unit], explicit_unit, 1.0
    compact = context.replace(" ", "")
    for unit in ("조원", "억원", "백만원", "천원", "원"):
        if f"단위:{unit}" in compact or f"단위：{unit}" in compact or f"({unit})" in compact:
            return _UNIT_SCALE[unit], unit, 0.85
    return 1.0, "원(추정)", 0.55


def extract_disclosure_amount(text: str, event_type: str) -> tuple[float | None, dict[str, Any]]:
    """Extract the disclosed investment/contract amount from DART original text.

    Returns the largest plausible amount adjacent to a field label. The function
    deliberately ignores ratios and dates and reports confidence/source metadata
    so downstream scoring can fall back to event counts when extraction coverage
    is weak.
    """
    if not text or event_type not in _AMOUNT_LABELS:
        return None, {"status": "NO_TEXT_OR_UNSUPPORTED"}
    normalized = re.sub(r"[\u00a0\t\r]+", " ", text)
    candidates: list[dict[str, Any]] = []
    for label in _AMOUNT_LABELS[event_type]:
        pattern = re.compile(
            re.escape(label) + r"[^0-9%]{0,120}" + _AMOUNT_NUMBER + r"\s*" + _AMOUNT_UNIT,
            re.IGNORECASE,
        )
        for match in pattern.finditer(normalized):
            raw_number = match.group(1).replace(",", "")
            try:
                numeric = float(raw_number)
            except ValueError:
                continue
            context_start = max(0, match.start() - 160)
            context_end = min(len(normalized), match.end() + 80)
            context = normalized[context_start:context_end]
            scale, unit, unit_confidence = _infer_unit_scale(context, match.group(2))
            amount = numeric * scale
            # Remove obvious table indices, dates, percentages, and implausible magnitudes.
            if amount < 1_000_000 or amount > 5e15 or "%" in match.group(0):
                continue
            candidates.append(
                {
                    "amount_krw": amount,
                    "label": label,
                    "unit": unit,
                    "confidence": unit_confidence,
                    "matched_text": match.group(0)[:220],
                }
            )
    if not candidates:
        return None, {"status": "NOT_FOUND"}
    # DART tables may repeat the same field. Choosing the largest labeled amount
    # is safer than summing duplicate table/summary copies.
    best = max(candidates, key=lambda row: row["amount_krw"])
    best["status"] = "FOUND"
    best["candidate_count"] = len(candidates)
    return float(best["amount_krw"]), best


def enrich_disclosure_amount(row: dict[str, Any], document_text: str) -> dict[str, Any]:
    output = dict(row)
    event_type = classify_disclosure(str(row.get("report_nm") or ""))
    output["event_type"] = event_type
    if not event_type:
        return output
    amount, metadata = extract_disclosure_amount(document_text, event_type)
    output["event_amount_krw"] = amount
    output["event_amount_metadata"] = metadata
    return output


def _period_bounds(as_of: dt.date, periods: int, days_per_period: int) -> list[tuple[dt.date, dt.date]]:
    bounds = []
    for offset in range(periods - 1, -1, -1):
        end = as_of - dt.timedelta(days=offset * days_per_period)
        start = end - dt.timedelta(days=days_per_period - 1)
        bounds.append((start, end))
    return bounds


def event_series(
    disclosures_by_code: dict[str, list[dict[str, Any]]],
    stock_codes: list[str],
    as_of: dt.date,
    event_type: str,
    periods: int = 12,
    days_per_period: int = 91,
) -> dict[str, list[tuple[str, float]]]:
    output: dict[str, list[tuple[str, float]]] = {code: [] for code in stock_codes}
    bounds = _period_bounds(as_of, periods, days_per_period)
    for code in stock_codes:
        events: list[dt.date] = []
        for row in disclosures_by_code.get(code, []):
            category = row.get("event_type") or classify_disclosure(str(row.get("report_nm") or ""))
            if category != event_type:
                continue
            raw_date = str(row.get("rcept_dt") or "")
            if len(raw_date) == 8:
                try:
                    events.append(dt.datetime.strptime(raw_date, "%Y%m%d").date())
                except ValueError:
                    pass
        for start, end in bounds:
            count = sum(1 for event_date in events if start <= event_date <= end)
            output[code].append((end.isoformat(), float(count)))
    return output


def event_amount_series(
    disclosures_by_code: dict[str, list[dict[str, Any]]],
    stock_codes: list[str],
    as_of: dt.date,
    event_type: str,
    periods: int = 12,
    days_per_period: int = 91,
) -> tuple[dict[str, list[tuple[str, float]]], dict[str, Any]]:
    output: dict[str, list[tuple[str, float]]] = {code: [] for code in stock_codes}
    bounds = _period_bounds(as_of, periods, days_per_period)
    total_events = 0
    events_with_amount = 0
    per_company: dict[str, dict[str, int]] = {}
    for code in stock_codes:
        events: list[tuple[dt.date, float]] = []
        company_total = 0
        company_found = 0
        for row in disclosures_by_code.get(code, []):
            category = row.get("event_type") or classify_disclosure(str(row.get("report_nm") or ""))
            if category != event_type:
                continue
            company_total += 1
            total_events += 1
            raw_date = str(row.get("rcept_dt") or "")
            amount = row.get("event_amount_krw")
            if len(raw_date) != 8 or amount is None:
                continue
            try:
                event_date = dt.datetime.strptime(raw_date, "%Y%m%d").date()
                numeric_amount = float(amount)
            except (ValueError, TypeError):
                continue
            if not math.isfinite(numeric_amount) or numeric_amount <= 0:
                continue
            events.append((event_date, numeric_amount))
            company_found += 1
            events_with_amount += 1
        per_company[code] = {"total_events": company_total, "events_with_amount": company_found}
        for start, end in bounds:
            amount_sum = sum(amount for event_date, amount in events if start <= event_date <= end)
            output[code].append((end.isoformat(), amount_sum))
    quality = {
        "event_type": event_type,
        "total_events": total_events,
        "events_with_amount": events_with_amount,
        "amount_coverage": round(events_with_amount / max(1, total_events), 4),
        "per_company": per_company,
    }
    return output, quality


def normalize_event_amounts_by_revenue(
    amount_series: dict[str, list[tuple[str, float]]],
    revenue_series: dict[str, list[tuple[str, float]]],
) -> dict[str, list[tuple[str, float]]]:
    """Scale event amounts by each company's trailing-four-quarter revenue.

    The ratio prevents a single mega-cap from dominating a theme. When revenue
    is unavailable, a zero series is returned rather than inventing a scale.
    """
    output: dict[str, list[tuple[str, float]]] = {}
    for code, series in amount_series.items():
        revenues = [value for _, value in revenue_series.get(code, []) if value > 0]
        ttm_revenue = sum(revenues[-4:]) if len(revenues) >= 4 else None
        if not ttm_revenue:
            output[code] = [(date, 0.0) for date, _ in series]
            continue
        output[code] = [(date, amount / ttm_revenue) for date, amount in series]
    return output
