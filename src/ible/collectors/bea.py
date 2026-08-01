from __future__ import annotations

from typing import Any

from ible.http import JsonHttpClient


class BeaClient:
    BASE_URL = "https://apps.bea.gov/api/data"

    def __init__(self, api_key: str, http: JsonHttpClient) -> None:
        if not api_key:
            raise ValueError("BEA_API_KEY is required")
        self.api_key = api_key
        self.http = http

    def dataset_list(self) -> dict[str, Any]:
        return self.http.get_json(
            self.BASE_URL,
            params={
                "UserID": self.api_key,
                "method": "GETDATASETLIST",
                "ResultFormat": "JSON",
            },
            cache_key="bea_dataset_list",
            cache_ttl_seconds=604800,
        )

    def get_data(self, dataset_name: str, **parameters: Any) -> dict[str, Any]:
        params = {
            "UserID": self.api_key,
            "method": "GETDATA",
            "DatasetName": dataset_name,
            "ResultFormat": "JSON",
            **parameters,
        }
        key_parts = [dataset_name] + [f"{k}-{parameters[k]}" for k in sorted(parameters)]
        return self.http.get_json(
            self.BASE_URL,
            params=params,
            cache_key="bea_" + "_".join(key_parts).replace("/", "-"),
            cache_ttl_seconds=604800,
        )
