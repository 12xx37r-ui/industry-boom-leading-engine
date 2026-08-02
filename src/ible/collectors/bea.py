from __future__ import annotations

import re
from typing import Any

from ible.http import JsonHttpClient


class BeaClient:
    BASE_URLS = (
        "https://apps.bea.gov/api/data",
        "https://apps.bea.gov/api/data/",
    )

    UUID_PATTERN = re.compile(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    )

    def __init__(self, api_key: str, http: JsonHttpClient) -> None:
        raw_key = str(api_key or "")
        if not raw_key.strip():
            raise ValueError("BEA_API_KEY is required")

        # GitHub Secrets에 따옴표·제로폭 문자·복사 잔여문자가 섞여도
        # 공식 36자 UUID 본문이 있으면 자동으로 추출한다.
        uuid_match = self.UUID_PATTERN.search(raw_key)
        if uuid_match:
            normalized = uuid_match.group(0)
        else:
            normalized = "".join(
                ch for ch in raw_key
                if not ch.isspace() and ch not in {"\u200b", "\u200c", "\u200d", "\ufeff", '"', "'"}
            )

        self.api_key = normalized
        self.key_format_warning = None
        if len(self.api_key) != 36:
            # BEA는 보조 데이터원이다. 형식이 비정상이어도 엔진 전체를
            # 중단하지 않고 실제 API 응답을 bea_context.json에 기록한다.
            self.key_format_warning = (
                f"BEA_API_KEY unusual length after normalization: {len(self.api_key)}"
            )
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
                    "GETDATASETLIST": "GetDatasetList",
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
