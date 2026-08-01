from __future__ import annotations

import datetime as dt
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


def parse_amount(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


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
            candidates = _preferred_rows(group)
            row = candidates[0]
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


def event_series(
    disclosures_by_code: dict[str, list[dict[str, Any]]],
    stock_codes: list[str],
    as_of: dt.date,
    event_type: str,
    periods: int = 8,
    days_per_period: int = 91,
) -> dict[str, list[tuple[str, float]]]:
    output: dict[str, list[tuple[str, float]]] = {code: [] for code in stock_codes}
    for code in stock_codes:
        events: list[dt.date] = []
        for row in disclosures_by_code.get(code, []):
            category = classify_disclosure(str(row.get("report_nm") or ""))
            if category != event_type:
                continue
            raw_date = str(row.get("rcept_dt") or "")
            if len(raw_date) == 8:
                try:
                    events.append(dt.datetime.strptime(raw_date, "%Y%m%d").date())
                except ValueError:
                    pass
        for offset in range(periods - 1, -1, -1):
            end = as_of - dt.timedelta(days=offset * days_per_period)
            start = end - dt.timedelta(days=days_per_period - 1)
            count = sum(1 for event_date in events if start <= event_date <= end)
            output[code].append((end.isoformat(), float(count)))
    return output
