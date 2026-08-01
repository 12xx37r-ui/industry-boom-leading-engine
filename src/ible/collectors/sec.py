from __future__ import annotations

from typing import Any

from ible.http import JsonHttpClient


class SecClient:
    TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
    COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

    def __init__(self, http: JsonHttpClient) -> None:
        self.http = http
        self._ticker_map: dict[str, str] | None = None

    def ticker_map(self) -> dict[str, str]:
        if self._ticker_map is not None:
            return self._ticker_map
        payload = self.http.get_json(
            self.TICKERS_URL,
            cache_key="sec_company_tickers",
            cache_ttl_seconds=86400,
        )
        mapping: dict[str, str] = {}
        for item in payload.values():
            try:
                mapping[str(item["ticker"]).upper()] = f"{int(item['cik_str']):010d}"
            except (KeyError, TypeError, ValueError):
                continue
        self._ticker_map = mapping
        return mapping

    def companyfacts(self, ticker: str) -> dict[str, Any]:
        normalized = ticker.upper()
        cik = self.ticker_map().get(normalized)
        if not cik:
            raise KeyError(f"Ticker not found in SEC mapping: {ticker}")
        return self.http.get_json(
            self.COMPANYFACTS_URL.format(cik=cik),
            cache_key=f"sec_companyfacts_{cik}",
            cache_ttl_seconds=86400,
        )
