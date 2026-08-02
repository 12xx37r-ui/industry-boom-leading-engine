from __future__ import annotations

import io
import json
import zipfile
from typing import Any

import requests
from bs4 import BeautifulSoup

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
            headers={"User-Agent": self.http.user_agent, "Accept": "application/zip,application/octet-stream,*/*"},
            timeout=20,
        )
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            xml_data = archive.read(archive.namelist()[0]).decode("utf-8")
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
                "last_reprt_at": "Y",
                "page_count": page_count,
                "sort": "date",
                "sort_mth": "asc",
            },
            cache_key=f"opendart_list_v2_{stock_code}_{begin_date}_{end_date}",
            cache_ttl_seconds=21600,
        )
        status = payload.get("status")
        if status == "013":
            return []
        if status != "000":
            raise RuntimeError(f"OpenDART error {status}: {payload.get('message')}")
        return payload.get("list", [])

    def document_text(self, rcept_no: str) -> str:
        raw = self.http.get_bytes(
            f"{self.API_BASE}/document.xml",
            params={"crtfc_key": self.api_key, "rcept_no": rcept_no},
            headers={"Accept": "application/zip,application/octet-stream,*/*"},
            cache_key=f"opendart_document_{rcept_no}",
            cache_ttl_seconds=31536000,
            timeout=25,
        )
        files: list[bytes] = []
        if raw[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                for name in archive.namelist():
                    if name.lower().endswith((".xml", ".html", ".htm", ".txt")):
                        try:
                            files.append(archive.read(name))
                        except Exception:
                            continue
        else:
            files.append(raw)
        chunks: list[str] = []
        for blob in files:
            text = None
            for encoding in ("utf-8", "cp949", "euc-kr"):
                try:
                    text = blob.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            if not text:
                text = blob.decode("utf-8", errors="ignore")
            soup = BeautifulSoup(text, "xml" if text.lstrip().startswith("<?xml") else "html.parser")
            for tag in soup(["script", "style"]):
                tag.decompose()
            chunks.append(soup.get_text(" ", strip=True))
        return "\n".join(chunks)

    def full_accounts(self, stock_code: str, business_year: int, report_code: str) -> list[dict[str, Any]]:
        corp_code = self.stock_to_corp_map().get(stock_code)
        if not corp_code:
            return []
        last_error: Exception | None = None
        for fs_div in ("CFS", "OFS"):
            try:
                payload = self.http.get_json(
                    f"{self.API_BASE}/fnlttSinglAcntAll.json",
                    params={
                        "crtfc_key": self.api_key,
                        "corp_code": corp_code,
                        "bsns_year": str(business_year),
                        "reprt_code": report_code,
                        "fs_div": fs_div,
                    },
                    cache_key=f"opendart_full_{stock_code}_{business_year}_{report_code}_{fs_div}",
                    cache_ttl_seconds=86400,
                )
                status = payload.get("status")
                if status == "000":
                    return payload.get("list", [])
                if status == "013":
                    continue
                last_error = RuntimeError(f"OpenDART full accounts error {status}: {payload.get('message')}")
            except Exception as exc:
                last_error = exc
        if last_error:
            raise last_error
        return []

    def major_accounts_multi(self, stock_codes: list[str], business_year: int, report_code: str) -> list[dict[str, Any]]:
        mapping = self.stock_to_corp_map()
        corp_codes = [mapping[code] for code in stock_codes if code in mapping]
        if not corp_codes:
            return []
        payload = self.http.get_json(
            f"{self.API_BASE}/fnlttMultiAcnt.json",
            params={
                "crtfc_key": self.api_key,
                "corp_code": ",".join(corp_codes[:100]),
                "bsns_year": str(business_year),
                "reprt_code": report_code,
            },
            cache_key=f"opendart_multi_{business_year}_{report_code}_{len(corp_codes)}",
            cache_ttl_seconds=86400,
        )
        status = payload.get("status")
        if status == "013":
            return []
        if status != "000":
            raise RuntimeError(f"OpenDART financials error {status}: {payload.get('message')}")
        return payload.get("list", [])
