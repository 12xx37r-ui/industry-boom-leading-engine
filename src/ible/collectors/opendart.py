from __future__ import annotations

import io
import json
import zipfile
from typing import Any

import requests

from ible.http import JsonHttpClient


class OpenDartClient:
    API_BASE = "https://opendart.fss.or.kr/api"
    CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"

    def __init__(self, api_key: str, http: JsonHttpClient) -> None:
        if not api_key:
            raise ValueError("OPENDART_API_KEY is required")
        self.api_key = api_key
        self.http = http
        self._stock_to_corp: dict[str, str] | None = None

    def stock_to_corp_map(self) -> dict[str, str]:
        if self._stock_to_corp is not None:
            return self._stock_to_corp
        cache_path = self.http.cache_dir / "opendart_corp_codes.json" if self.http.cache_dir else None
        if cache_path and cache_path.exists():
            with cache_path.open("r", encoding="utf-8") as handle:
                self._stock_to_corp = json.load(handle)
                return self._stock_to_corp
        response = requests.get(
            self.CORP_CODE_URL,
            params={"crtfc_key": self.api_key},
            headers={"User-Agent": self.http.user_agent},
            timeout=15,
        )
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            xml_data = archive.read(archive.namelist()[0]).decode("utf-8")
        # Deliberately avoid an extra XML dependency.
        import xml.etree.ElementTree as ET

        root = ET.fromstring(xml_data)
        mapping: dict[str, str] = {}
        for node in root.findall("list"):
            stock_code = (node.findtext("stock_code") or "").strip()
            corp_code = (node.findtext("corp_code") or "").strip()
            if stock_code and corp_code:
                mapping[stock_code] = corp_code
        if cache_path:
            with cache_path.open("w", encoding="utf-8") as handle:
                json.dump(mapping, handle, ensure_ascii=False)
        self._stock_to_corp = mapping
        return mapping

    def disclosures(self, stock_code: str, begin_date: str, end_date: str, page_count: int = 100) -> list[dict[str, Any]]:
        corp_code = self.stock_to_corp_map().get(stock_code)
        if not corp_code:
            return []
        payload = self.http.get_json(
            f"{self.API_BASE}/list.json",
            params={
                "crtfc_key": self.api_key,
                "corp_code": corp_code,
                "bgn_de": begin_date,
                "end_de": end_date,
                "page_count": page_count,
                "sort": "date",
                "sort_mth": "desc",
            },
            cache_key=f"opendart_list_{stock_code}_{begin_date}_{end_date}",
            cache_ttl_seconds=21600,
        )
        status = payload.get("status")
        if status == "013":
            return []
        if status != "000":
            raise RuntimeError(f"OpenDART error {status}: {payload.get('message')}")
        return payload.get("list", [])
