from __future__ import annotations

from typing import Any

from ible.http import JsonHttpClient


class BeaClient:
    BASE_URLS = (
        "https://apps.bea.gov/api/data/",
        "https://apps.bea.gov/api/data",
    )

    def __init__(self, api_key: str, http: JsonHttpClient) -> None:
        if not api_key:
            raise ValueError("BEA_API_KEY is required")
        self.api_key = api_key.strip()
        self.http = http

    @staticmethod
    def _api_error(payload: dict[str, Any]) -> dict[str, Any] | None:
        results = payload.get("BEAAPI", {}).get("Results", {})
        if isinstance(results, dict):
            error = results.get("Error")
            if isinstance(error, dict):
                return error
        return None

    def _call(self, params: dict[str, Any], cache_key: str) -> dict[str, Any]:
        attempts: list[tuple[str, dict[str, Any]]] = []
        for base in self.BASE_URLS:
            canonical = dict(params)
            attempts.append((base, canonical))
            if "method" in canonical:
                title_case = dict(canonical)
                method = str(title_case["method"])
                title_case["method"] = {
                    "GETDATASETLIST": "GetDataSetList",
                    "GETPARAMETERLIST": "GetParameterList",
                    "GETPARAMETERVALUES": "GetParameterValues",
                    "GETDATA": "GetData",
                }.get(method.upper(), method)
                attempts.append((base, title_case))
        errors: list[Any] = []
        for index, (base, candidate) in enumerate(attempts):
            try:
                payload = self.http.get_json(
                    base,
                    params=candidate,
                    cache_key=f"{cache_key}_{index}",
                    cache_ttl_seconds=604800,
                )
            except Exception as exc:
                errors.append(str(exc))
                continue
            error = self._api_error(payload)
            if not error:
                return payload
            errors.append(error)
            description = str(error.get("APIErrorDescription") or "")
            if "Invalid Request" not in description and "Invalid Parameters" not in description:
                return payload
        return {
            "BEAAPI": {
                "Results": {
                    "Error": {
                        "APIErrorCode": "CLIENT_FALLBACK_EXHAUSTED",
                        "APIErrorDescription": "All canonical BEA request variants failed.",
                        "attempt_errors": errors,
                    }
                }
            }
        }

    def dataset_list(self) -> dict[str, Any]:
        return self._call(
            {
                "UserID": self.api_key,
                "method": "GETDATASETLIST",
                "ResultFormat": "JSON",
            },
            "bea_dataset_list",
        )

    def parameter_values(self, dataset_name: str, parameter_name: str) -> dict[str, Any]:
        return self._call(
            {
                "UserID": self.api_key,
                "method": "GETPARAMETERVALUES",
                "DatasetName": dataset_name,
                "ParameterName": parameter_name,
                "ResultFormat": "JSON",
            },
            f"bea_parameter_values_{dataset_name}_{parameter_name}",
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
        return self._call(params, "bea_" + "_".join(key_parts).replace("/", "-"))

    def fixed_asset_table_catalog(self) -> dict[str, Any]:
        return self.parameter_values("FixedAssets", "TableName")
