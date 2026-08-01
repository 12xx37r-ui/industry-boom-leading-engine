from __future__ import annotations

from typing import Any

from ible.http import JsonHttpClient


class FredClient:
    BASE_URL = "https://api.stlouisfed.org/fred"

    def __init__(self, api_key: str, http: JsonHttpClient) -> None:
        if not api_key:
            raise ValueError("FRED_API_KEY is required")
        self.api_key = api_key
        self.http = http

    def observations(
        self,
        series_id: str,
        *,
        observation_start: str,
        observation_end: str,
        as_of: str | None = None,
    ) -> list[dict[str, Any]]:
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
        payload = self.http.get_json(
            f"{self.BASE_URL}/series/observations",
            params=params,
            cache_key=f"fred_{series_id}_{as_of or 'latest'}_{observation_start}_{observation_end}",
            cache_ttl_seconds=86400,
        )
        return payload.get("observations", [])
