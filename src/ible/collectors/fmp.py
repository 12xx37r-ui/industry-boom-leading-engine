from __future__ import annotations

import datetime as dt
import json
import math
import os
import time
from pathlib import Path
from typing import Any, Iterable

import requests


class FmpError(RuntimeError):
    pass


class FmpClient:
    """Download point-in-time quarterly statements from Financial Modeling Prep.

    V0.8.4 performs one real preflight request before downloading the cohort.  A
    bad key, unavailable endpoint, or subscription restriction therefore fails
    immediately with the provider's response instead of retrying every ticker.
    """

    STABLE_BASE = "https://financialmodelingprep.com/stable"
    LEGACY_BASE = "https://financialmodelingprep.com/api/v3"
    METRICS = ("capex", "rd", "revenue", "gross_profit", "operating_income")
    PERMANENT_HTTP_CODES = {400, 401, 402, 403, 404, 422}

    def __init__(
        self,
        cache_dir: str | Path,
        api_key: str,
        timeout: int = 45,
        min_interval: float = 0.35,
        quarter_limit: int | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.subset_dir = self.cache_dir / "subset"
        self.status_path = self.cache_dir / "fmp_download_status.json"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.subset_dir.mkdir(parents=True, exist_ok=True)
        self.api_key = self._sanitize_api_key(api_key)
        self.timeout = int(timeout)
        self.min_interval = max(0.25, float(min_interval))
        configured_limit = quarter_limit or int(os.getenv("FMP_QUARTER_LIMIT", "20"))
        self.quarter_limit = max(8, min(20, int(configured_limit)))
        self._last_request_at = 0.0
        self.session = requests.Session()
        self.preflight_result: dict[str, Any] | None = None

    @staticmethod
    def _sanitize_api_key(value: str) -> str:
        text = str(value or "").strip().strip('"').strip("'")
        for prefix in ("?apikey=", "&apikey=", "apikey="):
            if text.lower().startswith(prefix):
                text = text[len(prefix):].strip()
                break
        return text

    def _redact(self, text: str) -> str:
        if self.api_key:
            return str(text).replace(self.api_key, "***")
        return str(text)

    def validate_api_key(self) -> None:
        if len(self.api_key) < 8 or any(ch.isspace() for ch in self.api_key):
            raise FmpError("FMP_API_KEY secret is missing or malformed; store only the key value")

    def _respect_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request_at = time.monotonic()

    def _response_detail(self, response: requests.Response) -> str:
        body = ""
        try:
            body = response.text[:500].replace("\n", " ").strip()
        except Exception:  # noqa: BLE001
            pass
        detail = f"HTTP {response.status_code}"
        if body:
            detail += f" body={body}"
        return self._redact(detail)

    @staticmethod
    def _payload_message(payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        for key in ("Error Message", "error", "message", "detail", "status"):
            value = payload.get(key)
            if value and not isinstance(value, (dict, list)):
                return str(value)
        return None

    @classmethod
    def _is_permanent_message(cls, message: str) -> bool:
        lowered = message.lower()
        permanent_terms = (
            "invalid api key",
            "missing api key",
            "subscription",
            "upgrade",
            "not available under your current subscription",
            "not authorized",
            "unauthorized",
            "forbidden",
            "endpoint not available",
        )
        return any(term in lowered for term in permanent_terms)

    def _request_rows(
        self,
        url: str,
        params: dict[str, Any],
        *,
        purpose: str,
    ) -> list[dict[str, Any]]:
        last_error = "unknown error"
        for attempt in range(1, 4):
            try:
                self._respect_rate_limit()
                response = self.session.get(
                    url,
                    params={**params, "apikey": self.api_key},
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "IndustryBoomLeadingEngine/0.8.4",
                    },
                    timeout=(15, self.timeout),
                )
                if response.status_code >= 400:
                    detail = self._response_detail(response)
                    if response.status_code in self.PERMANENT_HTTP_CODES:
                        raise FmpError(f"{purpose}: permanent access failure: {detail}")
                    raise RuntimeError(f"{purpose}: transient provider failure: {detail}")
                try:
                    payload = response.json()
                except Exception as exc:  # noqa: BLE001
                    raise RuntimeError(f"{purpose}: invalid JSON response") from exc
                message = self._payload_message(payload)
                if message:
                    clean = self._redact(message)
                    if self._is_permanent_message(clean):
                        raise FmpError(f"{purpose}: permanent access failure: {clean}")
                    raise RuntimeError(f"{purpose}: provider error: {clean}")
                if not isinstance(payload, list):
                    raise RuntimeError(f"{purpose}: response is not a statement list")
                return [row for row in payload if isinstance(row, dict)]
            except FmpError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = self._redact(str(exc))
                if attempt < 3:
                    time.sleep(2**attempt)
        raise FmpError(last_error)

    def _statement_rows(
        self,
        ticker: str,
        statement: str,
        *,
        limit: int,
    ) -> tuple[list[dict[str, Any]], str]:
        stable_url = f"{self.STABLE_BASE}/{statement}"
        legacy_url = f"{self.LEGACY_BASE}/{statement}/{ticker}"
        errors: list[str] = []
        candidates = (
            (stable_url, {"symbol": ticker, "period": "quarter", "limit": limit}),
            (legacy_url, {"period": "quarter", "limit": limit}),
        )
        for url, params in candidates:
            try:
                rows = self._request_rows(
                    url,
                    params,
                    purpose=f"{statement} {ticker}",
                )
                if rows:
                    return rows, url
                errors.append(f"{url}: empty response")
            except FmpError as exc:
                errors.append(str(exc))
                # A stable-endpoint plan restriction does not prove the legacy
                # endpoint is unavailable, so try both official forms once.
                continue
        raise FmpError(" | ".join(errors))

    def preflight(self) -> dict[str, Any]:
        """Validate the exact quarterly request used by the cohort.

        The API key is supplied as the ``apikey`` query parameter by requests.
        The key itself is never printed or written to diagnostics.
        """
        self.validate_api_key()
        try:
            rows, source = self._statement_rows(
                "AAPL",
                "income-statement",
                limit=min(20, self.quarter_limit),
            )
            result = {
                "status": "OK",
                "ticker": "AAPL",
                "period": "quarter",
                "limit": self.quarter_limit,
                "rows": len(rows),
                "source": source,
            }
            self.preflight_result = result
            print(
                f"[FMP-PREFLIGHT] OK rows={len(rows)} source={source} "
                f"period=quarter limit={self.quarter_limit}",
                flush=True,
            )
            return result
        except Exception as exc:  # noqa: BLE001
            detail = self._redact(str(exc))
            result = {
                "status": "FAILED",
                "ticker": "AAPL",
                "period": "quarter",
                "limit": self.quarter_limit,
                "error": detail,
            }
            self.preflight_result = result
            self.status_path.write_text(
                json.dumps(
                    {
                        "status": "PREFLIGHT_FAILED",
                        "provider": "financialmodelingprep",
                        "preflight": result,
                        "requested": 0,
                        "downloaded": 0,
                        "available": 0,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(f"[FMP-PREFLIGHT] FAILED {detail}", flush=True)
            raise FmpError(
                "FMP preflight failed before cohort download. "
                "The API key or quarterly-statement access is unavailable. "
                f"Provider response: {detail}"
            ) from exc

    def _download_ticker(self, ticker: str) -> dict[str, Any]:
        income, income_source = self._statement_rows(
            ticker,
            "income-statement",
            limit=self.quarter_limit,
        )
        cashflow, cashflow_source = self._statement_rows(
            ticker,
            "cash-flow-statement",
            limit=self.quarter_limit,
        )
        if not income and not cashflow:
            raise FmpError("empty income and cash-flow statements")
        return {
            "ticker": ticker,
            "period_mode": "quarter",
            "quarter_limit": self.quarter_limit,
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
        remaining = [ticker for ticker in requested if ticker not in existing]
        if remaining:
            self.preflight()

        errors: dict[str, str] = {}
        downloaded = 0
        print(
            f"[FMP] companies={len(requested)} cached={len(existing)} download={len(remaining)} "
            f"quarter_limit={self.quarter_limit}",
            flush=True,
        )

        for index, ticker in enumerate(remaining, start=1):
            try:
                payload = self._download_ticker(ticker)
                (self.subset_dir / f"{ticker}.json").write_text(
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8",
                )
                downloaded += 1
            except Exception as exc:  # noqa: BLE001
                errors[ticker] = self._redact(str(exc))
            if index == len(remaining) or index % 5 == 0:
                first_error = next(iter(errors.values()), "")
                suffix = f" first_error={first_error[:220]}" if first_error else ""
                print(
                    f"[FMP] {index}/{len(remaining)} downloaded={downloaded} "
                    f"errors={len(errors)}{suffix}",
                    flush=True,
                )

        present = [ticker for ticker in requested if (self.subset_dir / f"{ticker}.json").exists()]
        missing = [ticker for ticker in requested if ticker not in present]
        result = {
            "status": "COMPLETE" if not missing else "PARTIAL" if present else "UNAVAILABLE",
            "provider": "financialmodelingprep",
            "period": "quarter",
            "quarter_limit": self.quarter_limit,
            "preflight": self.preflight_result,
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
            first_error = next(iter(result["errors"].values()), "unknown provider response")
            raise FmpError(
                f"FMP financial statements unavailable for all {len(requested)} companies. "
                f"First error: {first_error}"
            )
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
            period = str(row.get("period") or "").upper()
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
                errors[ticker] = self._redact(str(exc))
        return output, errors
