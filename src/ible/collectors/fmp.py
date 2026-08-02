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
    """Free-plan-compatible FMP collector.

    V0.8.5 uses only the current ``/stable`` endpoints, hard-limits every
    request to five annual rows, and never calls the retired legacy API.  The
    2021 holdout snapshot is reconstructed from the latest available FY row
    plus FMP's year-over-year growth row.  This avoids asking the free plan for
    historical quarterly depth that it does not provide.

    Provider restrictions are fail-soft: diagnostics are written and the
    workflow can still produce an ``INSUFFICIENT_DATA`` result rather than a
    red workflow failure.
    """

    STABLE_BASE = "https://financialmodelingprep.com/stable"
    METRICS = ("capex", "rd", "revenue", "gross_profit", "operating_income")
    FREE_LIMIT = 5
    PERMANENT_HTTP_CODES = {400, 401, 402, 403, 404, 422}
    ENDPOINTS = (
        "income-statement",
        "cash-flow-statement",
        "financial-growth",
    )

    def __init__(
        self,
        cache_dir: str | Path,
        api_key: str,
        timeout: int = 45,
        min_interval: float = 0.35,
        annual_limit: int | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.subset_dir = self.cache_dir / "subset"
        self.status_path = self.cache_dir / "fmp_download_status.json"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.subset_dir.mkdir(parents=True, exist_ok=True)
        self.api_key = self._sanitize_api_key(api_key)
        self.timeout = int(timeout)
        self.min_interval = max(0.25, float(min_interval))
        configured = annual_limit or int(os.getenv("FMP_ANNUAL_LIMIT", str(self.FREE_LIMIT)))
        self.annual_limit = max(1, min(self.FREE_LIMIT, int(configured)))
        self._last_request_at = 0.0
        self.session = requests.Session()
        self.preflight_result: dict[str, Any] | None = None

    @staticmethod
    def _sanitize_api_key(value: str) -> str:
        text = str(value or "").strip().strip('"').strip("'")
        for prefix in ("?apikey=", "&apikey=", "apikey="):
            if text.lower().startswith(prefix):
                text = text[len(prefix) :].strip()
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
            body = response.text[:700].replace("\n", " ").strip()
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
            "legacy endpoint",
            "premium query parameter",
        )
        return any(term in lowered for term in permanent_terms)

    def _request_rows(
        self,
        endpoint: str,
        ticker: str,
    ) -> list[dict[str, Any]]:
        """Request one current stable endpoint with free-plan-safe params."""
        url = f"{self.STABLE_BASE}/{endpoint}"
        params = {
            "symbol": ticker,
            "period": "annual",
            "limit": self.annual_limit,
            "apikey": self.api_key,
        }
        last_error = "unknown error"
        for attempt in range(1, 3):
            try:
                self._respect_rate_limit()
                response = self.session.get(
                    url,
                    params=params,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "IndustryBoomLeadingEngine/0.8.5",
                    },
                    timeout=(15, self.timeout),
                )
                if response.status_code >= 400:
                    detail = self._response_detail(response)
                    if response.status_code in self.PERMANENT_HTTP_CODES:
                        raise FmpError(f"{endpoint} {ticker}: permanent access failure: {detail}")
                    raise RuntimeError(f"{endpoint} {ticker}: transient provider failure: {detail}")
                try:
                    payload = response.json()
                except Exception as exc:  # noqa: BLE001
                    raise RuntimeError(f"{endpoint} {ticker}: invalid JSON response") from exc
                message = self._payload_message(payload)
                if message:
                    clean = self._redact(message)
                    if self._is_permanent_message(clean):
                        raise FmpError(f"{endpoint} {ticker}: permanent access failure: {clean}")
                    raise RuntimeError(f"{endpoint} {ticker}: provider error: {clean}")
                if not isinstance(payload, list):
                    raise RuntimeError(f"{endpoint} {ticker}: response is not a list")
                return [row for row in payload if isinstance(row, dict)]
            except FmpError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = self._redact(str(exc))
                if attempt < 2:
                    time.sleep(2)
        raise FmpError(last_error)

    def preflight(self) -> dict[str, Any]:
        """Probe the exact free-plan annual routes without raising outward."""
        try:
            self.validate_api_key()
        except FmpError as exc:
            result = {
                "status": "FAILED",
                "ticker": "AAPL",
                "period": "annual",
                "limit": self.annual_limit,
                "working_endpoints": [],
                "errors": {"api_key": str(exc)},
            }
            self.preflight_result = result
            print(f"[FMP-PREFLIGHT] FAILED {exc}", flush=True)
            return result

        working: list[str] = []
        errors: dict[str, str] = {}
        row_counts: dict[str, int] = {}
        for endpoint in self.ENDPOINTS:
            try:
                rows = self._request_rows(endpoint, "AAPL")
                row_counts[endpoint] = len(rows)
                if rows:
                    working.append(endpoint)
                else:
                    errors[endpoint] = "empty response"
            except Exception as exc:  # noqa: BLE001
                errors[endpoint] = self._redact(str(exc))

        if "income-statement" in working:
            status = "OK" if len(working) == len(self.ENDPOINTS) else "PARTIAL"
        else:
            status = "FAILED"
        result = {
            "status": status,
            "ticker": "AAPL",
            "period": "annual",
            "limit": self.annual_limit,
            "working_endpoints": working,
            "row_counts": row_counts,
            "errors": errors,
        }
        self.preflight_result = result
        print(
            f"[FMP-PREFLIGHT] {status} working={len(working)}/{len(self.ENDPOINTS)} "
            f"limit={self.annual_limit} endpoints={','.join(working) or 'none'}",
            flush=True,
        )
        if errors:
            first = next(iter(errors.values()))
            print(f"[FMP-PREFLIGHT] first_error={first[:350]}", flush=True)
        return result

    def _download_ticker(self, ticker: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ticker": ticker,
            "period_mode": "annual",
            "annual_limit": self.annual_limit,
            "method": "free_plan_annual_plus_growth_bridge",
            "endpoint_errors": {},
            "provenance": {
                "provider": "financialmodelingprep",
                "base": self.STABLE_BASE,
                "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            },
        }
        endpoint_to_key = {
            "income-statement": "income_statement",
            "cash-flow-statement": "cash_flow_statement",
            "financial-growth": "financial_growth",
        }
        available = 0
        for endpoint in self.ENDPOINTS:
            key = endpoint_to_key[endpoint]
            try:
                rows = self._request_rows(endpoint, ticker)
                payload[key] = rows
                if rows:
                    available += 1
            except Exception as exc:  # noqa: BLE001
                payload[key] = []
                payload["endpoint_errors"][endpoint] = self._redact(str(exc))
        if available == 0:
            raise FmpError("all current stable annual endpoints returned no usable data")
        return payload

    def prepare_subset(self, tickers: Iterable[str], force: bool = False) -> dict[str, Any]:
        requested = sorted({str(t).upper() for t in tickers})
        if force:
            for ticker in requested:
                (self.subset_dir / f"{ticker}.json").unlink(missing_ok=True)

        existing = [ticker for ticker in requested if (self.subset_dir / f"{ticker}.json").exists()]
        remaining = [ticker for ticker in requested if ticker not in existing]
        preflight = self.preflight() if remaining else None

        errors: dict[str, str] = {}
        downloaded = 0
        print(
            f"[FMP] companies={len(requested)} cached={len(existing)} download={len(remaining)} "
            f"period=annual limit={self.annual_limit}",
            flush=True,
        )

        if remaining and preflight and preflight.get("status") == "FAILED":
            first = next(iter((preflight.get("errors") or {}).values()), "preflight unavailable")
            errors = {ticker: first for ticker in remaining}
        else:
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
            "period": "annual",
            "annual_limit": self.annual_limit,
            "method": "free_plan_annual_plus_growth_bridge",
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
            print(
                "[FMP] no statements available; continuing with an insufficient-data result instead of failing",
                flush=True,
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
    def _filed_date(cls, row: dict[str, Any]) -> str | None:
        for key in ("filingDate", "fillingDate", "acceptedDate"):
            value = cls._date_text(row, key)
            if value:
                return value
        return None

    @staticmethod
    def _year(row: dict[str, Any]) -> int | None:
        for key in ("calendarYear", "fiscalYear", "year"):
            value = row.get(key)
            try:
                return int(str(value)[:4])
            except (TypeError, ValueError):
                pass
        value = row.get("date")
        try:
            return int(str(value)[:4])
        except (TypeError, ValueError):
            return None

    @classmethod
    def _annual_rows(cls, rows: list[dict[str, Any]], as_of: str) -> list[dict[str, Any]]:
        cutoff = dt.date.fromisoformat(as_of)
        by_year: dict[int, dict[str, Any]] = {}
        for row in rows:
            period = str(row.get("period") or "FY").upper()
            if period not in {"FY", "ANNUAL", "YEAR", ""}:
                continue
            end = cls._date_text(row, "date")
            filed = cls._filed_date(row)
            year = cls._year(row)
            if year is None or not end:
                continue
            if dt.date.fromisoformat(end) > cutoff:
                continue
            if filed and dt.date.fromisoformat(filed) > cutoff:
                continue
            old = by_year.get(year)
            if old is None:
                by_year[year] = row
                continue
            old_filed = cls._filed_date(old) or ""
            if (filed or "") > old_filed:
                by_year[year] = row
        return [by_year[key] for key in sorted(by_year)]

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
    def _matching_growth_row(
        cls,
        rows: list[dict[str, Any]],
        current: dict[str, Any],
    ) -> dict[str, Any] | None:
        current_year = cls._year(current)
        current_date = cls._date_text(current, "date")
        for row in rows:
            if current_date and cls._date_text(row, "date") == current_date:
                return row
            if current_year is not None and cls._year(row) == current_year:
                return row
        return None

    @classmethod
    def _annual_bridge_series(
        cls,
        current_row: dict[str, Any],
        previous_row: dict[str, Any] | None,
        growth_row: dict[str, Any] | None,
        value_fields: tuple[str, ...],
        growth_fields: tuple[str, ...],
        *,
        absolute: bool = False,
    ) -> list[tuple[str, float]]:
        current = cls._value(current_row, value_fields, absolute=absolute)
        if current is None:
            return []

        previous = cls._value(previous_row or {}, value_fields, absolute=absolute)
        if previous is None and growth_row:
            growth = cls._value(growth_row, growth_fields)
            if growth is not None and growth > -0.98:
                denominator = 1.0 + growth
                if abs(denominator) > 1e-9:
                    previous = current / denominator
        if previous is None or previous <= 0 or current < 0:
            return []

        year = cls._year(current_row)
        if year is None:
            return []
        prior_quarter = previous / 4.0
        current_quarter = current / 4.0
        if prior_quarter <= 0:
            return []

        if current_quarter > 0:
            ratio = current_quarter / prior_quarter
            if ratio > 0:
                quarterly_ratio = ratio ** 0.25
            else:
                quarterly_ratio = 1.0
        else:
            quarterly_ratio = 1.0

        dates = [
            f"{year - 1}-03-31",
            f"{year - 1}-06-30",
            f"{year - 1}-09-30",
            f"{year - 1}-12-31",
            f"{year}-03-31",
            f"{year}-06-30",
            f"{year}-09-30",
            f"{year}-12-31",
        ]
        values = [prior_quarter] * 4
        for step in range(1, 5):
            values.append(prior_quarter * (quarterly_ratio**step))
        return [(date, float(value)) for date, value in zip(dates, values)]

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
            for metric in self.METRICS:
                output[metric][ticker] = []
            if not path.exists():
                errors[ticker] = "statement cache missing"
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                income = self._annual_rows(payload.get("income_statement", []), as_of)
                cashflow = self._annual_rows(payload.get("cash_flow_statement", []), as_of)
                financial_growth = payload.get("financial_growth", [])
                if not income and not cashflow:
                    errors[ticker] = "no annual statement filed by holdout cutoff"
                    continue

                current_income = income[-1] if income else None
                previous_income = income[-2] if len(income) >= 2 else None
                current_cashflow = cashflow[-1] if cashflow else None
                previous_cashflow = cashflow[-2] if len(cashflow) >= 2 else None

                if current_income:
                    growth = self._matching_growth_row(financial_growth, current_income)
                    output["revenue"][ticker] = self._annual_bridge_series(
                        current_income,
                        previous_income,
                        growth,
                        ("revenue",),
                        ("growthRevenue", "revenueGrowth"),
                    )
                    output["gross_profit"][ticker] = self._annual_bridge_series(
                        current_income,
                        previous_income,
                        growth,
                        ("grossProfit",),
                        ("growthGrossProfit", "grossProfitGrowth"),
                    )
                    output["operating_income"][ticker] = self._annual_bridge_series(
                        current_income,
                        previous_income,
                        growth,
                        ("operatingIncome",),
                        ("growthOperatingIncome", "operatingIncomeGrowth"),
                        absolute=True,
                    )
                    output["rd"][ticker] = self._annual_bridge_series(
                        current_income,
                        previous_income,
                        growth,
                        (
                            "researchAndDevelopmentExpenses",
                            "researchAndDevelopmentExpense",
                            "researchDevelopment",
                        ),
                        (
                            "growthResearchAndDevelopmentExpenses",
                            "growthResearchAndDevelopmentExpense",
                            "researchAndDevelopmentExpensesGrowth",
                        ),
                        absolute=True,
                    )

                if current_cashflow:
                    growth = self._matching_growth_row(financial_growth, current_cashflow)
                    output["capex"][ticker] = self._annual_bridge_series(
                        current_cashflow,
                        previous_cashflow,
                        growth,
                        ("capitalExpenditure", "investmentsInPropertyPlantAndEquipment"),
                        ("growthCapitalExpenditure", "capitalExpenditureGrowth"),
                        absolute=True,
                    )

                usable = sum(bool(output[metric][ticker]) for metric in self.METRICS)
                if usable == 0:
                    errors[ticker] = "annual statements found but no prior-year bridge could be built"
            except Exception as exc:  # noqa: BLE001
                errors[ticker] = self._redact(str(exc))
        return output, errors
