from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from ible.v3_http import JsonHttpClient


@dataclass(frozen=True)
class Period:
    start: date
    end: date

    def as_dict(self) -> dict[str, str]:
        return {"start_date": self.start.isoformat(), "end_date": self.end.isoformat()}


def comparison_periods(as_of: date, lookback_days: int) -> tuple[Period, Period]:
    recent_end = as_of
    recent_start = recent_end - timedelta(days=lookback_days - 1)
    prior_end = recent_start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=lookback_days - 1)
    return Period(recent_start, recent_end), Period(prior_start, prior_end)


class OpenAlexCollector:
    def __init__(self, client: JsonHttpClient, base_url: str) -> None:
        self.client = client
        self.base_url = base_url

    def count(self, query: str, period: Period) -> int:
        payload = self.client.request_json(
            self.base_url,
            params={
                "search": query,
                "filter": f"from_publication_date:{period.start.isoformat()},to_publication_date:{period.end.isoformat()}",
                "per-page": 1,
                "select": "id",
            },
        )
        meta = payload.get("meta") or {}
        return max(0, int(meta.get("count") or 0))


class UsaSpendingCollector:
    AWARD_TYPE_CODES = ["02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "A", "B", "C", "D", "IDV_A", "IDV_B", "IDV_B_A", "IDV_B_B", "IDV_B_C", "IDV_C", "IDV_D", "IDV_E"]

    def __init__(self, client: JsonHttpClient, base_url: str) -> None:
        self.client = client
        self.base_url = base_url

    def count(self, keywords: list[str], period: Period) -> int:
        payload = self.client.request_json(
            self.base_url,
            method="POST",
            payload={
                "filters": {
                    "keywords": keywords,
                    "time_period": [period.as_dict()],
                    "award_type_codes": self.AWARD_TYPE_CODES,
                },
                "spending_level": "awards",
                "subawards": False,
            },
        )
        results = payload.get("results") or {}
        fields = ("grants", "loans", "contracts", "direct_payments", "other", "idvs")
        return sum(max(0, int(results.get(field) or 0)) for field in fields)
