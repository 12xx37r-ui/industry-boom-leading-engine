from __future__ import annotations

import datetime as dt
import json
import math
import time
from pathlib import Path
from typing import Any, Iterable

import requests


class FmpError(RuntimeError):
    pass


class FmpClient:
    """Download standardized quarterly statements from Financial Modeling Prep.

    This collector exists as a network-independent alternative to SEC EDGAR for
    GitHub-hosted runners, whose shared IPs can be rejected by SEC.  Raw payloads
    are cached per ticker and all observations are filtered by filing date before
    the historical as-of date is scored.
    """

    STABLE_BASE = "https://financialmodelingprep.com/stable"
    LEGACY_BASE = "https://financialmodelingprep.com/api/v3"
    METRICS = ("capex", "rd", "revenue", "gross_profit", "operating_income")

    def __init__(
        self,
        cache_dir: str | Path,
        api_key: str,
        timeout: int = 45,
        min_interval: float = 0.35,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.subset_dir = self.cache_dir / "subset"
        self.status_path = self.cache_dir / "fmp_download_status.json"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.subset_dir.mkdir(parents=True, exist_ok=True)
        self.api_key = api_key.strip()
        self.timeout = int(timeout)
        self.min_interval = max(0.25, float(min_interval))
        self._last_request_at = 0.0
        self.session = requests.Session()

    def validate_api_key(self) -> None:
        if len(self.api_key) < 8:
            raise FmpError("FMP_API_KEY secret is missing or invalid")

    def _respect_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request_at = time.monotonic()

    @staticmethod
    def _response_detail(response: requests.Response) -> str:
        body = ""
        try:
            body = response.text[:300].replace("\n", " ").strip()
        except Exception:  # noqa: BLE001
            pass
        detail = f"HTTP {response.status_code}"
        if body:
            detail += f" body={body}"
        return detail

    @staticmethod
    def _validate_rows(payload: Any, endpoint: str) -> list[dict[str, Any]]:
        if isinstance(payload, dict):
            message = payload.get("Error Message") or payload.get("error") or payload.get("message")
            if message:
                raise FmpError(f"{endpoint}: {message}")
        if not isinstance(payload, list):
            raise FmpError(f"{endpoint}: response is not a statement list")
        return [row for row in payload if isinstance(row, dict)]

    def _get_json(self, urls: list[str], params: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
        last_error = "unknown error"
        for url in urls:
            endpoint_name = url.rsplit("/", 1)[-1]
            for attempt in range(1, 4):
                try:
                    self._respect_rate_limit()
                    response = self.session.get(
                        url,
                        params={**params, "apikey": self.api_key},
                        headers={"Accept": "application/json", "User-Agent": "IndustryBoomLeadingEngine/0.8.3"},
                        timeout=(15, self.timeout),
                    )
                    if response.status_code >= 400:
                        raise FmpError(self._response_detail(response))
                    rows = self._validate_rows(response.json(), endpoint_name)
                    return rows, url
                except Exception as exc:  # noqa: BLE001
                    last_error = str(exc)
                    if attempt < 3:
                        time.sleep(2**attempt)
        raise FmpError(last_error)

    def _download_ticker(self, ticker: str) -> dict[str, Any]:
        income, income_source = self._get_json(
            [
                f"{self.STABLE_BASE}/income-statement",
                f"{self.LEGACY_BASE}/income-statement/{ticker}",
            ],
            {"symbol": ticker, "period": "quarter", "limit": 80},
        )
        cashflow, cashflow_source = self._get_json(
            [
                f"{self.STABLE_BASE}/cash-flow-statement",
                f"{self.LEGACY_BASE}/cash-flow-statement/{ticker}",
            ],
            {"symbol": ticker, "period": "quarter", "limit": 80},
        )
        if not income and not cashflow:
            raise FmpError("empty income and cash-flow statements")
        return {
            "ticker": ticker,
            "income_statement": income,
            "cash_flow_statement": cashflow,
            "provenance": {
                "provider": "financialmodelingprep",
                "income_source": income_source,
                "cashflow_source": cashflow_source,
                "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            },
        }

    def prepare_subset(self, tickers: Iterable[str], force: bool = False) -> dict[str, Any]:
        self.validate_api_key()
        requested = sorted({str(t).upper() for t in tickers})
        if force:
            for ticker in requested:
                (self.subset_dir / f"{ticker}.json").unlink(missing_ok=True)

        existing = [ticker for ticker in requested if (self.subset_dir / f"{ticker}.json").exists()]
        errors: dict[str, str] = {}
        downloaded = 0
        remaining = [ticker for ticker in requested if ticker not in existing]
        print(f"[FMP] companies={len(requested)} cached={len(existing)} download={len(remaining)}", flush=True)

        for index, ticker in enumerate(remaining, start=1):
            try:
                payload = self._download_ticker(ticker)
                (self.subset_dir / f"{ticker}.json").write_text(
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8",
                )
                downloaded += 1
            except Exception as exc:  # noqa: BLE001
                errors[ticker] = str(exc)
            if index == len(remaining) or index % 5 == 0:
                print(
                    f"[FMP] {index}/{len(remaining)} downloaded={downloaded} errors={len(errors)}",
                    flush=True,
                )

        present = [ticker for ticker in requested if (self.subset_dir / f"{ticker}.json").exists()]
        missing = [ticker for ticker in requested if ticker not in present]
        result = {
            "status": "COMPLETE" if not missing else "PARTIAL" if present else "UNAVAILABLE",
            "provider": "financialmodelingprep",
            "requested": len(requested),
            "cached": len(existing),
            "downloaded": downloaded,
            "available": len(present),
            "missing": missing,
            "errors": {ticker: errors.get(ticker, "missing cached statement") for ticker in missing},
            "subset_dir": str(self.subset_dir),
        }
        self.status_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        if not present:
            raise FmpError(f"FMP financial statements unavailable for all {len(requested)} companies")
        return result

    @staticmethod
    def _date_text(row: dict[str, Any], key: str) -> str | None:
        value = row.get(key)
        if not value:
            return None
        text = str(value)[:10]
        try:
            dt.date.fromisoformat(text)
        except ValueError:
            return None
        return text

    @classmethod
    def _quarter_rows(cls, rows: list[dict[str, Any]], as_of: str) -> list[dict[str, Any]]:
        cutoff = dt.date.fromisoformat(as_of)
        by_period: dict[str, dict[str, Any]] = {}
        for row in rows:
            period = str(row.get("period") or row.get("calendarYear") or "").upper()
            if period and period not in {"Q1", "Q2", "Q3", "Q4"}:
                continue
            end = cls._date_text(row, "date")
            filed = cls._date_text(row, "filingDate") or cls._date_text(row, "acceptedDate")
            if not end or not filed:
                continue
            if dt.date.fromisoformat(end) > cutoff or dt.date.fromisoformat(filed) > cutoff:
                continue
            old = by_period.get(end)
            if old is None:
                by_period[end] = row
                continue
            old_filed = cls._date_text(old, "filingDate") or cls._date_text(old, "acceptedDate") or ""
            if filed > old_filed:
                by_period[end] = row
        return [by_period[key] for key in sorted(by_period)]

    @staticmethod
    def _value(row: dict[str, Any], fields: tuple[str, ...], absolute: bool = False) -> float | None:
        for field in fields:
            value = row.get(field)
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(number):
                continue
            return abs(number) if absolute else number
        return None

    @classmethod
    def _series_from_rows(
        cls,
        rows: list[dict[str, Any]],
        fields: tuple[str, ...],
        *,
        absolute: bool = False,
    ) -> list[tuple[str, float]]:
        result: list[tuple[str, float]] = []
        for row in rows:
            end = cls._date_text(row, "date")
            value = cls._value(row, fields, absolute=absolute)
            if end and value is not None:
                result.append((end, value))
        return result[-16:]

    def load_series(
        self,
        tickers: Iterable[str],
        as_of: str,
    ) -> tuple[dict[str, dict[str, list[tuple[str, float]]]], dict[str, str]]:
        output: dict[str, dict[str, list[tuple[str, float]]]] = {
            metric: {} for metric in self.METRICS
        }
        errors: dict[str, str] = {}
        for ticker in sorted({str(t).upper() for t in tickers}):
            path = self.subset_dir / f"{ticker}.json"
            if not path.exists():
                errors[ticker] = "statement cache missing"
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                income = self._quarter_rows(payload.get("income_statement", []), as_of)
                cashflow = self._quarter_rows(payload.get("cash_flow_statement", []), as_of)
                output["revenue"][ticker] = self._series_from_rows(income, ("revenue",))
                output["gross_profit"][ticker] = self._series_from_rows(income, ("grossProfit",))
                output["operating_income"][ticker] = self._series_from_rows(income, ("operatingIncome",))
                output["rd"][ticker] = self._series_from_rows(
                    income,
                    ("researchAndDevelopmentExpenses", "researchAndDevelopmentExpense", "researchDevelopment"),
                    absolute=True,
                )
                output["capex"][ticker] = self._series_from_rows(
                    cashflow,
                    ("capitalExpenditure", "investmentsInPropertyPlantAndEquipment"),
                    absolute=True,
                )
            except Exception as exc:  # noqa: BLE001
                errors[ticker] = str(exc)
        return output, errors
