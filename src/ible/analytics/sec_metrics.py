from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict
from typing import Any

FLOW_TAGS = {
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsForAdditionsToPropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ],
    "rd": ["ResearchAndDevelopmentExpense"],
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
}

STOCK_TAGS = {
    "inventory": ["InventoryNet", "InventoryNetOfAllowancesCustomerAdvancesAndProgressBillings"],
}


def _parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def _duration_days(item: dict[str, Any]) -> int | None:
    if not item.get("start") or not item.get("end"):
        return None
    return (_parse_date(item["end"]) - _parse_date(item["start"])).days


def _pick_unit(fact: dict[str, Any]) -> list[dict[str, Any]]:
    units = fact.get("units", {})
    for unit in ("USD", "USD/shares", "shares"):
        if isinstance(units.get(unit), list):
            return units[unit]
    for values in units.values():
        if isinstance(values, list):
            return values
    return []


def find_fact(companyfacts: dict[str, Any], tags: list[str]) -> tuple[str | None, dict[str, Any] | None]:
    facts = companyfacts.get("facts", {})
    for taxonomy in ("us-gaap", "ifrs-full"):
        tax = facts.get(taxonomy, {})
        for tag in tags:
            if tag in tax:
                return tag, tax[tag]
    return None, None


def quarterly_flow(companyfacts: dict[str, Any], tags: list[str], as_of: str) -> tuple[str | None, list[tuple[str, float]]]:
    tag, fact = find_fact(companyfacts, tags)
    if not fact:
        return None, []
    cutoff = _parse_date(as_of)
    candidates: list[dict[str, Any]] = []
    for item in _pick_unit(fact):
        try:
            if item.get("form") not in {"10-Q", "10-K", "20-F", "40-F"}:
                continue
            if not item.get("filed") or _parse_date(item["filed"]) > cutoff:
                continue
            if not item.get("end") or _parse_date(item["end"]) > cutoff:
                continue
            value = float(item["val"])
            if not math.isfinite(value):
                continue
            duration = _duration_days(item)
            if duration is None or duration < 55 or duration > 390:
                continue
            record = dict(item)
            record["_duration"] = duration
            record["_value"] = value
            candidates.append(record)
        except (KeyError, TypeError, ValueError):
            continue

    # Keep the most recently filed record for each economic period.
    by_period: dict[tuple[str, str], dict[str, Any]] = {}
    for item in candidates:
        key = (item.get("start", ""), item.get("end", ""))
        old = by_period.get(key)
        if old is None or item.get("filed", "") > old.get("filed", ""):
            by_period[key] = item
    items = sorted(by_period.values(), key=lambda x: (x["end"], x["_duration"]))

    quarters: dict[str, float] = {}
    # Direct fiscal/Calendar quarter values, generally 55-115 days.
    for item in items:
        if 55 <= item["_duration"] <= 115:
            quarters[item["end"]] = item["_value"]

    # Derive quarterly values from cumulative YTD periods when direct values are absent.
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        grouped[item.get("fy") or item["end"][:4]].append(item)
    for _, year_items in grouped.items():
        year_items = sorted(year_items, key=lambda x: x["end"])
        previous_cumulative: float | None = None
        previous_end: str | None = None
        direct_sum = 0.0
        for item in year_items:
            duration = item["_duration"]
            value = item["_value"]
            end = item["end"]
            if 55 <= duration <= 115:
                direct_sum += value
                previous_cumulative = None
                previous_end = end
                continue
            if 120 <= duration <= 300:
                derived = value if previous_cumulative is None else value - previous_cumulative
                if end not in quarters and math.isfinite(derived):
                    quarters[end] = derived
                previous_cumulative = value
                previous_end = end
            elif 300 < duration <= 390:
                # Annual minus available first three quarter values within roughly the same fiscal year.
                fiscal_start = _parse_date(item["start"])
                prior = [
                    v
                    for q_end, v in quarters.items()
                    if fiscal_start <= _parse_date(q_end) < _parse_date(end)
                ]
                derived = value - sum(prior[-3:]) if len(prior) >= 3 else None
                if end not in quarters and derived is not None and math.isfinite(derived):
                    quarters[end] = derived
                previous_cumulative = None
                previous_end = end

    result = sorted(quarters.items(), key=lambda x: x[0])
    return tag, result[-16:]


def latest_stock(companyfacts: dict[str, Any], tags: list[str], as_of: str) -> tuple[str | None, list[tuple[str, float]]]:
    tag, fact = find_fact(companyfacts, tags)
    if not fact:
        return None, []
    cutoff = _parse_date(as_of)
    by_end: dict[str, dict[str, Any]] = {}
    for item in _pick_unit(fact):
        try:
            if item.get("form") not in {"10-Q", "10-K", "20-F", "40-F"}:
                continue
            if not item.get("filed") or _parse_date(item["filed"]) > cutoff:
                continue
            if not item.get("end") or _parse_date(item["end"]) > cutoff:
                continue
            old = by_end.get(item["end"])
            if old is None or item.get("filed", "") > old.get("filed", ""):
                by_end[item["end"]] = item
        except (TypeError, ValueError):
            continue
    result: list[tuple[str, float]] = []
    for end, item in sorted(by_end.items()):
        try:
            result.append((end, float(item["val"])))
        except (KeyError, TypeError, ValueError):
            pass
    return tag, result[-16:]
