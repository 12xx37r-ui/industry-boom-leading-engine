from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ible.http import JsonHttpClient


class SecClient:
    """SEC Company Facts client using a repository-vendored ticker→CIK map.

    GitHub-hosted runners are sometimes denied by SEC's ``www.sec.gov/files``
    ticker-list endpoint even when ``data.sec.gov`` remains available. The
    configured universe is therefore resolved from an audited local map so one
    blocked metadata request cannot take down the whole engine.
    """

    COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

    def __init__(self, http: JsonHttpClient, cik_map_path: str | Path) -> None:
        self.http = http
        self.cik_map_path = Path(cik_map_path)
        self._ticker_map: dict[str, str] | None = None

    def ticker_map(self) -> dict[str, str]:
        if self._ticker_map is not None:
            return self._ticker_map
        if not self.cik_map_path.exists():
            raise FileNotFoundError(f"SEC CIK map not found: {self.cik_map_path}")
        payload = json.loads(self.cik_map_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("SEC CIK map must be a JSON object")
        mapping: dict[str, str] = {}
        for ticker, cik in payload.items():
            normalized = str(ticker).strip().upper()
            digits = "".join(ch for ch in str(cik) if ch.isdigit())
            if normalized and digits:
                mapping[normalized] = f"{int(digits):010d}"
        self._ticker_map = mapping
        return mapping

    def companyfacts(self, ticker: str) -> dict[str, Any]:
        normalized = ticker.upper()
        cik = self.ticker_map().get(normalized)
        if not cik:
            raise KeyError(f"Ticker missing from local SEC CIK map: {ticker}")
        return self.http.get_json(
            self.COMPANYFACTS_URL.format(cik=cik),
            cache_key=f"sec_companyfacts_{cik}",
            cache_ttl_seconds=86400,
        )
