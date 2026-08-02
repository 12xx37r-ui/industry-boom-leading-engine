from __future__ import annotations

import csv
import io
from typing import Any

from ible.http import HttpError, JsonHttpClient


class FredClient:
    API_BASE_URL = "https://api.stlouisfed.org/fred"
    CSV_BASE_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

    def __init__(self, api_key: str, http: JsonHttpClient) -> None:
        self.api_key = api_key.strip()
        self.http = http

    def observations(
        self,
        series_id: str,
        *,
        observation_start: str,
        observation_end: str,
        as_of: str | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Return rows plus source metadata.

        Primary path is the registered FRED/ALFRED API. GitHub-hosted runners can be
        denied with HTTP 403; in that case the official FRED graph CSV endpoint is
        used. CSV fallback is revised-current-vintage data truncated at the requested
        date, not a true ALFRED vintage.
        """
        api_error: str | None = None
        if self.api_key:
            params: dict[str, Any] = {
                "api_key": self.api_key,
                "file_type": "json",
                "series_id": series_id,
                "observation_start": observation_start,
                "observation_end": observation_end,
                "sort_order": "asc",
            }
            if as_of:
                params["realtime_start"] = as_of
                params["realtime_end"] = as_of
            try:
                payload = self.http.get_json(
                    f"{self.API_BASE_URL}/series/observations",
                    params=params,
                    cache_key=f"fred_api_{series_id}_{as_of or 'latest'}_{observation_start}_{observation_end}",
                    cache_ttl_seconds=86400,
                )
                rows = payload.get("observations", [])
                return rows, {
                    "transport": "FRED_API",
                    "vintage_mode": "ALFRED_POINT_IN_TIME" if as_of else "LATEST",
                    "fallback_used": False,
                }
            except Exception as exc:
                api_error = str(exc)

        csv_params = {
            "id": series_id,
            "cosd": observation_start,
            "coed": observation_end,
        }
        try:
            text = self.http.get_text(
                self.CSV_BASE_URL,
                params=csv_params,
                cache_key=f"fred_csv_{series_id}_{observation_start}_{observation_end}",
                cache_ttl_seconds=86400,
            )
            reader = csv.DictReader(io.StringIO(text))
            rows: list[dict[str, Any]] = []
            for row in reader:
                date_value = row.get("DATE") or row.get("observation_date") or row.get("date")
                value = row.get(series_id)
                if value is None:
                    value = next((v for k, v in row.items() if k != "DATE"), None)
                if not date_value:
                    continue
                if as_of and date_value > as_of:
                    continue
                rows.append({"date": date_value, "value": value})
            return rows, {
                "transport": "FRED_OFFICIAL_CSV",
                "vintage_mode": "REVISED_CUTOFF" if as_of else "LATEST",
                "fallback_used": True,
                "api_error": api_error,
                "warning": (
                    "FRED API가 차단되어 공식 CSV를 사용했습니다. 과거 재현값은 당시 빈티지가 아니라 "
                    "현재 수정값을 기준일에서 잘라 쓴 값입니다."
                ),
            }
        except Exception as csv_exc:
            detail = f"FRED API error={api_error}; CSV fallback error={csv_exc}"
            raise HttpError(detail) from csv_exc
