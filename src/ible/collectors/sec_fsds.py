from __future__ import annotations

import csv
import datetime as dt
import io
import json
import math
import os
import time
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import requests

from ible.analytics.sec_metrics import FLOW_TAGS


class SecFsdsError(RuntimeError):
    """Expected failure while downloading or parsing SEC Financial Statement Data Sets."""


DEFAULT_PERIODS = ("2021q1", "2021q2", "2021q3", "2021q4", "2022q1", "2022q2")
BASE_URL = "https://www.sec.gov/files/dera/data/financial-statement-data-sets/{period}.zip"
FORMS = {"10-K", "10-Q", "20-F", "40-F"}
METRICS = ("capex", "rd", "revenue", "gross_profit", "operating_income")


def _iso_date(value: str) -> dt.date:
    value = str(value or "").strip().replace("-", "")
    if len(value) != 8 or not value.isdigit():
        raise ValueError(f"invalid SEC date: {value!r}")
    return dt.date(int(value[:4]), int(value[4:6]), int(value[6:8]))


def _float(value: str) -> float:
    number = float(str(value).strip())
    if not math.isfinite(number):
        raise ValueError("non-finite SEC number")
    return number


def _normalize_cik(value: str | int) -> str:
    return str(int(str(value).strip())).zfill(10)


def _reader(zf: zipfile.ZipFile, filename: str) -> Iterable[dict[str, str]]:
    names = {name.lower(): name for name in zf.namelist()}
    actual = names.get(filename.lower())
    if not actual:
        raise SecFsdsError(f"{filename} missing from SEC dataset archive")
    raw = zf.open(actual, "r")
    text = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="")
    return csv.DictReader(text, delimiter="\t")


def _metric_from_tags(tags: list[str]) -> str | None:
    requested = set(tags)
    for metric, candidates in FLOW_TAGS.items():
        if requested == set(candidates) or requested.intersection(candidates):
            return metric
    return None


def _dedupe_single_tag(records: list[dict[str, Any]], tag: str) -> list[dict[str, Any]]:
    """Keep one consolidated fact per economic end-date and duration.

    SEC FSDS repeats comparative facts in later filings.  We use the latest filing
    available by the point-in-time cutoff, but keep fiscal-year metadata so a YTD
    value can only be differenced against a prior cumulative value from the same
    fiscal year.
    """
    selected: dict[tuple[str, int], dict[str, Any]] = {}
    for record in records:
        if str(record.get("tag") or "") != tag:
            continue
        try:
            key = (str(record["ddate"]), int(record["qtrs"]))
        except (KeyError, TypeError, ValueError):
            continue
        old = selected.get(key)
        if old is None:
            selected[key] = record
            continue
        old_filed = str(old.get("filed") or "")
        new_filed = str(record.get("filed") or "")
        old_form = str(old.get("form") or "")
        new_form = str(record.get("form") or "")
        preferred_form = "10-K" if key[1] == 4 else "10-Q"
        if new_filed > old_filed or (
            new_filed == old_filed and new_form == preferred_form and old_form != preferred_form
        ):
            selected[key] = record
    return sorted(
        selected.values(),
        key=lambda row: (str(row["ddate"]), int(row["qtrs"]), str(row.get("filed") or "")),
    )


def _fiscal_match(current: dict[str, Any], prior: dict[str, Any]) -> bool:
    current_fy = str(current.get("fy") or "").strip()
    prior_fy = str(prior.get("fy") or "").strip()
    if current_fy and prior_fy:
        return current_fy == prior_fy
    # Some foreign/private issuers omit FY.  In that case only allow a very local
    # predecessor whose end dates fall in the same calendar year.
    try:
        return _iso_date(str(current["ddate"])).year == _iso_date(str(prior["ddate"])).year
    except (KeyError, TypeError, ValueError):
        return False


def _aligned_yoy(values: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Return date-aligned year-over-year changes.

    Index-minus-four is unsafe when a quarter is missing or duplicate.  Match each
    point to the closest observation 300-430 days earlier instead.
    """
    parsed = [(_iso_date(date), float(value)) for date, value in values]
    result: list[tuple[str, float]] = []
    for index, (current_date, current_value) in enumerate(parsed):
        choices: list[tuple[int, float]] = []
        for prior_date, prior_value in parsed[:index]:
            gap = (current_date - prior_date).days
            if 300 <= gap <= 430 and prior_value != 0:
                choices.append((abs(gap - 365), (current_value - prior_value) / abs(prior_value)))
        if choices:
            choices.sort(key=lambda item: item[0])
            result.append((current_date.isoformat(), choices[0][1]))
    return result


def _series_for_tag(
    records: list[dict[str, Any]],
    tag: str,
    metric: str | None,
) -> tuple[list[tuple[str, float]], dict[str, Any]]:
    rows = _dedupe_single_tag(records, tag)
    direct: dict[str, tuple[float, str, str, dict[str, Any]]] = {}
    cumulative: dict[int, list[dict[str, Any]]] = defaultdict(list)
    rejected_negative = 0
    rejected_cross_fiscal = 0
    derived_count = 0
    nonnegative_metric = metric in {"revenue", "capex", "rd", "gross_profit"}

    def accept(value: float) -> bool:
        nonlocal rejected_negative
        if not math.isfinite(value):
            return False
        if nonnegative_metric and value < 0:
            rejected_negative += 1
            return False
        return True

    for row in rows:
        qtrs = int(row["qtrs"])
        if qtrs == 1:
            value = float(row["value"])
            if not accept(value):
                continue
            cumulative[1].append(row)
            end = str(row["ddate"])
            old = direct.get(end)
            filed = str(row.get("filed") or "")
            if old is None or filed > old[1]:
                direct[end] = (value, filed, "direct", row)
        elif qtrs in {2, 3, 4}:
            cumulative[qtrs].append(row)

    def nearest_previous(current: dict[str, Any], expected_qtrs: int) -> dict[str, Any] | None:
        nonlocal rejected_cross_fiscal
        current_date = _iso_date(str(current["ddate"]))
        choices: list[tuple[int, str, dict[str, Any]]] = []
        had_local_nonmatch = False
        for candidate in cumulative.get(expected_qtrs, []):
            candidate_date = _iso_date(str(candidate["ddate"]))
            gap = (current_date - candidate_date).days
            if not 45 <= gap <= 140:
                continue
            if not _fiscal_match(current, candidate):
                had_local_nonmatch = True
                continue
            choices.append((abs(gap - 91), str(candidate.get("filed") or ""), candidate))
        if not choices:
            if had_local_nonmatch:
                rejected_cross_fiscal += 1
            return None
        choices.sort(key=lambda item: (item[0], item[1]))
        best_distance = choices[0][0]
        tied = [item for item in choices if item[0] == best_distance]
        return max(tied, key=lambda item: item[1])[2]

    for qtrs in (2, 3, 4):
        for row in sorted(
            cumulative.get(qtrs, []),
            key=lambda item: (str(item["ddate"]), str(item.get("filed") or "")),
        ):
            end = str(row["ddate"])
            if end in direct:
                continue
            prior = nearest_previous(row, qtrs - 1)
            derived: float | None = None
            if prior is not None:
                derived = float(row["value"]) - float(prior["value"])
            elif qtrs == 4:
                annual_end = _iso_date(end)
                current_fy = str(row.get("fy") or "").strip()
                candidates: list[tuple[dt.date, float]] = []
                for date, (value, _, _, source_row) in direct.items():
                    qdate = _iso_date(date)
                    gap = (annual_end - qdate).days
                    if not 45 <= gap <= 330:
                        continue
                    source_fy = str(source_row.get("fy") or "").strip()
                    if current_fy and source_fy and current_fy != source_fy:
                        continue
                    candidates.append((qdate, value))
                candidates.sort(key=lambda item: item[0])
                prior_three = candidates[-3:]
                if len(prior_three) == 3:
                    dates = [item[0] for item in prior_three] + [annual_end]
                    gaps = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
                    if all(45 <= gap <= 140 for gap in gaps):
                        derived = float(row["value"]) - sum(value for _, value in prior_three)
            if derived is not None and accept(derived):
                direct[end] = (derived, str(row.get("filed") or ""), f"derived_qtrs_{qtrs}", row)
                derived_count += 1

    result = [(date, value) for date, (value, _, _, _) in sorted(direct.items())]
    # Keep only the trailing uninterrupted fiscal-quarter run.  A missing quarter
    # makes index-minus-four comparisons invalid, so stale points before the gap
    # must not remain in the scored series.
    if len(result) >= 5:
        contiguous: list[tuple[str, float]] = [result[0]]
        for item in result[1:]:
            gap = (_iso_date(item[0]) - _iso_date(contiguous[-1][0])).days
            if 45 <= gap <= 140:
                contiguous.append(item)
            else:
                contiguous = [item]
        result = contiguous[-16:]

    continuity_gaps = [
        (_iso_date(result[index][0]) - _iso_date(result[index - 1][0])).days
        for index in range(1, len(result))
    ]
    continuity = (
        sum(1 for gap in continuity_gaps if 45 <= gap <= 140) / len(continuity_gaps)
        if continuity_gaps
        else 0.0
    )
    yoy_rows = _aligned_yoy(result)
    limit = {"revenue": 5.0, "gross_profit": 5.0, "operating_income": 10.0, "rd": 10.0, "capex": 25.0}.get(metric, 10.0)
    extreme_yoy = sum(1 for _, value in yoy_rows if abs(value) > limit)
    audit = {
        "tag": tag,
        "input_records": len(rows),
        "quarter_count": len(result),
        "direct_count": sum(1 for _, (_, _, source, _) in direct.items() if source == "direct"),
        "derived_count": derived_count,
        "rejected_negative": rejected_negative,
        "rejected_cross_fiscal": rejected_cross_fiscal,
        "continuity_ratio": round(continuity, 4),
        "aligned_yoy_count": len(yoy_rows),
        "extreme_yoy_count": extreme_yoy,
        "max_abs_yoy": round(max((abs(value) for _, value in yoy_rows), default=0.0), 6),
        "trailing_contiguous": True,
    }
    return result, audit



def _annual_proxy_for_tag(
    records: list[dict[str, Any]],
    tag: str,
    metric: str | None,
) -> tuple[list[tuple[str, float]], dict[str, Any]]:
    """Build a conservative quarterly proxy from annual consolidated flow facts.

    This is used only when a reliable quarter-by-quarter series cannot be formed.
    Each annual flow is divided equally across four synthetic quarter points.  The
    proxy preserves annual year-over-year growth while avoiding unsafe subtraction
    across missing or misaligned fiscal quarters.
    """
    rows = _dedupe_single_tag(records, tag)
    nonnegative_metric = metric in {"revenue", "capex", "rd", "gross_profit"}
    annual: dict[str, tuple[float, str]] = {}
    rejected_negative = 0
    rejected_nonannual = 0
    for row in rows:
        try:
            qtrs = int(row.get("qtrs") or 0)
            value = float(row.get("value"))
            end = str(row.get("ddate") or "")
        except (TypeError, ValueError):
            continue
        form = str(row.get("form") or "").strip()
        fp = str(row.get("fp") or "").strip().upper()
        if qtrs != 4 or not (form in {"10-K", "20-F", "40-F"} or fp == "FY"):
            rejected_nonannual += 1
            continue
        if not math.isfinite(value):
            continue
        if nonnegative_metric and value < 0:
            rejected_negative += 1
            continue
        filed = str(row.get("filed") or "")
        old = annual.get(end)
        if old is None or filed > old[1]:
            annual[end] = (value, filed)

    annual_values = [(date, value) for date, (value, _) in sorted(annual.items())]
    annual_growth: list[float] = []
    for index in range(1, len(annual_values)):
        prior = annual_values[index - 1][1]
        current = annual_values[index][1]
        if prior != 0:
            annual_growth.append((current - prior) / abs(prior))
    limit = {
        "revenue": 5.0,
        "gross_profit": 5.0,
        "operating_income": 10.0,
        "rd": 10.0,
        "capex": 25.0,
    }.get(metric, 10.0)
    extreme_yoy = sum(1 for value in annual_growth if abs(value) > limit)

    proxy: list[tuple[str, float]] = []
    for end_text, annual_value in annual_values[-3:]:
        end = _iso_date(end_text)
        quarterly_value = annual_value / 4.0
        for days in (273, 182, 91, 0):
            proxy.append(((end - dt.timedelta(days=days)).isoformat(), quarterly_value))
    # Dedupe rare overlaps from 52/53-week fiscal calendars.
    proxy = sorted({date: value for date, value in proxy}.items())[-12:]
    continuity_gaps = [
        (_iso_date(proxy[index][0]) - _iso_date(proxy[index - 1][0])).days
        for index in range(1, len(proxy))
    ]
    continuity = (
        sum(1 for gap in continuity_gaps if 45 <= gap <= 140) / len(continuity_gaps)
        if continuity_gaps
        else 0.0
    )
    audit = {
        "tag": tag,
        "frequency": "annual_proxy",
        "input_records": len(rows),
        "annual_count": len(annual_values),
        "quarter_count": len(proxy),
        "direct_count": 0,
        "derived_count": len(proxy),
        "rejected_negative": rejected_negative,
        "rejected_nonannual": rejected_nonannual,
        "rejected_cross_fiscal": 0,
        "continuity_ratio": round(continuity, 4),
        "aligned_yoy_count": max(0, 4 * (len(annual_values[-3:]) - 1)),
        "extreme_yoy_count": extreme_yoy,
        "max_abs_yoy": round(max((abs(value) for value in annual_growth), default=0.0), 6),
        "trailing_contiguous": True,
        "proxy_method": "annual_flow_divided_equally_into_four_quarters",
    }
    return proxy, audit

def build_quarterly_series_detailed(
    records: list[dict[str, Any]],
    tags: list[str],
    metric: str | None = None,
) -> tuple[str | None, list[tuple[str, float]], dict[str, Any]]:
    """Build a fiscal-safe series, using annual facts only as a conservative fallback."""
    metric = metric or _metric_from_tags(tags)
    quarterly_candidates: dict[str, dict[str, Any]] = {}
    best_quarterly: tuple[float, int, str, list[tuple[str, float]], dict[str, Any]] | None = None
    for priority, tag in enumerate(tags):
        values, audit = _series_for_tag(records, tag, metric)
        audit = {**audit, "frequency": "quarterly"}
        quarterly_candidates[tag] = audit
        quality = (
            4.0 * float(audit["aligned_yoy_count"])
            + 3.0 * float(audit["continuity_ratio"])
            + 0.5 * min(12.0, float(len(values)))
            - 8.0 * float(audit["extreme_yoy_count"])
            - 0.20 * float(audit["rejected_negative"])
            - 0.75 * float(audit["rejected_cross_fiscal"])
        )
        candidate = (quality, -priority, tag, values, audit)
        if best_quarterly is None or candidate[:2] > best_quarterly[:2]:
            best_quarterly = candidate

    if best_quarterly is not None and best_quarterly[3]:
        _, _, tag, values, selected_audit = best_quarterly
        quarterly_passed = bool(
            len(values) >= 5
            and int(selected_audit.get("aligned_yoy_count") or 0) >= 1
            and float(selected_audit.get("continuity_ratio") or 0) >= 0.75
            and not (metric == "revenue" and int(selected_audit.get("extreme_yoy_count") or 0) > 0)
        )
        if quarterly_passed:
            return tag, values[-12:], {
                "metric": metric,
                "selected_tag": tag,
                "selected": selected_audit,
                "candidates": quarterly_candidates,
                "annual_candidates": {},
                "mixed_tags": False,
                "selection_method": "strict_fiscal_quarter_series",
                "quality_passed": True,
                "fallback_used": False,
            }

    annual_candidates: dict[str, dict[str, Any]] = {}
    best_annual: tuple[float, int, str, list[tuple[str, float]], dict[str, Any]] | None = None
    for priority, tag in enumerate(tags):
        values, audit = _annual_proxy_for_tag(records, tag, metric)
        annual_candidates[tag] = audit
        quality = (
            6.0 * min(3.0, float(audit.get("annual_count") or 0))
            + 2.0 * float(audit.get("continuity_ratio") or 0)
            - 8.0 * float(audit.get("extreme_yoy_count") or 0)
            - 0.25 * float(audit.get("rejected_negative") or 0)
        )
        candidate = (quality, -priority, tag, values, audit)
        if best_annual is None or candidate[:2] > best_annual[:2]:
            best_annual = candidate

    if best_annual is not None and best_annual[3]:
        _, _, tag, values, selected_audit = best_annual
        annual_passed = bool(
            int(selected_audit.get("annual_count") or 0) >= 2
            and len(values) >= 8
            and float(selected_audit.get("continuity_ratio") or 0) >= 0.75
            and not (metric == "revenue" and int(selected_audit.get("extreme_yoy_count") or 0) > 0)
        )
        if annual_passed:
            return tag, values[-12:], {
                "metric": metric,
                "selected_tag": tag,
                "selected": selected_audit,
                "candidates": quarterly_candidates,
                "annual_candidates": annual_candidates,
                "mixed_tags": False,
                "selection_method": "annual_flow_proxy_fallback",
                "quality_passed": True,
                "fallback_used": True,
            }

    if best_quarterly is None or not best_quarterly[3]:
        return None, [], {
            "metric": metric,
            "selected_tag": None,
            "candidates": quarterly_candidates,
            "annual_candidates": annual_candidates,
            "quality_passed": False,
            "fallback_used": False,
        }
    _, _, tag, values, selected_audit = best_quarterly
    return tag, values[-12:], {
        "metric": metric,
        "selected_tag": tag,
        "selected": selected_audit,
        "candidates": quarterly_candidates,
        "annual_candidates": annual_candidates,
        "mixed_tags": False,
        "selection_method": "rejected_quarterly_no_safe_fallback",
        "quality_passed": False,
        "fallback_used": False,
    }

def build_quarterly_series(
    records: list[dict[str, Any]],
    tags: list[str],
    metric: str | None = None,
) -> tuple[str | None, list[tuple[str, float]]]:
    tag, values, _ = build_quarterly_series_detailed(records, tags, metric)
    return tag, values


class SecFsdsClient:
    def __init__(
        self,
        cache_dir: str | Path,
        user_agent: str,
        *,
        periods: Iterable[str] = DEFAULT_PERIODS,
        session: requests.Session | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.download_dir = self.cache_dir / "downloads"
        self.status_path = self.cache_dir / "sec_fsds_status.json"
        self.user_agent = str(user_agent or "").strip()
        self.periods = tuple(periods)
        self.session = session or requests.Session()

    def validate_user_agent(self) -> None:
        if len(self.user_agent) < 12 or "@" not in self.user_agent:
            raise SecFsdsError(
                "SEC_USER_AGENT must contain an application or organization name and a real contact email"
            )

    def _download(self, period: str, force: bool = False) -> Path:
        self.validate_user_agent()
        self.download_dir.mkdir(parents=True, exist_ok=True)
        destination = self.download_dir / f"{period}.zip"
        if destination.exists() and not force:
            try:
                with zipfile.ZipFile(destination) as zf:
                    if {name.lower() for name in zf.namelist()} >= {"sub.txt", "num.txt"}:
                        return destination
            except zipfile.BadZipFile:
                destination.unlink(missing_ok=True)

        url = BASE_URL.format(period=period)
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/zip,application/octet-stream;q=0.9,*/*;q=0.1",
            "Accept-Encoding": "identity",
            "Connection": "close",
        }
        last_error = "unknown error"
        for attempt in range(1, 3):
            temp = destination.with_suffix(".zip.part")
            temp.unlink(missing_ok=True)
            try:
                with self.session.get(url, headers=headers, stream=True, timeout=(20, 240)) as response:
                    if response.status_code != 200:
                        body = (response.text or "")[:350].replace("\n", " ")
                        raise SecFsdsError(f"HTTP {response.status_code} body={body}")
                    expected = int(response.headers.get("Content-Length") or 0)
                    received = 0
                    next_log = 25 * 1024 * 1024
                    with temp.open("wb") as fh:
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
                            if not chunk:
                                continue
                            fh.write(chunk)
                            received += len(chunk)
                            if received >= next_log:
                                print(
                                    f"[SEC-FSDS] {period} downloaded={received // (1024 * 1024)}MB",
                                    flush=True,
                                )
                                next_log += 25 * 1024 * 1024
                    if expected and received != expected:
                        raise SecFsdsError(f"truncated download expected={expected} received={received}")
                with zipfile.ZipFile(temp) as zf:
                    names = {name.lower() for name in zf.namelist()}
                    if not {"sub.txt", "num.txt"}.issubset(names):
                        raise SecFsdsError("downloaded ZIP does not contain sub.txt and num.txt")
                temp.replace(destination)
                print(f"[SEC-FSDS] {period} ready size={destination.stat().st_size}", flush=True)
                return destination
            except (requests.RequestException, OSError, zipfile.BadZipFile, SecFsdsError) as exc:
                last_error = str(exc)
                temp.unlink(missing_ok=True)
                if attempt < 2:
                    time.sleep(2)
        raise SecFsdsError(f"{period} download failed after 2 attempts: {last_error}")

    def _extract_records(
        self,
        archive: Path,
        cik_to_ticker: dict[str, str],
        cutoff: dt.date,
    ) -> tuple[set[str], list[dict[str, Any]]]:
        target_tags = {tag for metric in METRICS for tag in FLOW_TAGS[metric]}
        submissions: dict[str, dict[str, str]] = {}
        filing_tickers: set[str] = set()
        with zipfile.ZipFile(archive) as zf:
            for row in _reader(zf, "sub.txt"):
                try:
                    cik = _normalize_cik(row.get("cik", ""))
                    ticker = cik_to_ticker.get(cik)
                    if not ticker or str(row.get("form") or "").strip() not in FORMS:
                        continue
                    filed = _iso_date(row.get("filed", ""))
                    period = _iso_date(row.get("period", ""))
                    if filed > cutoff or period > cutoff:
                        continue
                    adsh = str(row.get("adsh") or "").strip()
                    if not adsh:
                        continue
                    submissions[adsh] = {
                        "ticker": ticker,
                        "filed": filed.isoformat(),
                        "period": period.isoformat(),
                        "fy": str(row.get("fy") or "").strip(),
                        "fp": str(row.get("fp") or "").strip(),
                        "form": str(row.get("form") or "").strip(),
                    }
                    filing_tickers.add(ticker)
                except (TypeError, ValueError):
                    continue

            records: list[dict[str, Any]] = []
            for row in _reader(zf, "num.txt"):
                adsh = str(row.get("adsh") or "").strip()
                submission = submissions.get(adsh)
                if not submission:
                    continue
                tag = str(row.get("tag") or "").strip()
                if tag not in target_tags:
                    continue
                if str(row.get("coreg") or "").strip():
                    continue
                if str(row.get("uom") or "").strip().upper() != "USD":
                    continue
                try:
                    qtrs = int(str(row.get("qtrs") or "0").strip())
                    if qtrs not in {1, 2, 3, 4}:
                        continue
                    ddate = _iso_date(row.get("ddate", ""))
                    if ddate > cutoff:
                        continue
                    value = _float(row.get("value", ""))
                except (TypeError, ValueError):
                    continue
                records.append(
                    {
                        **submission,
                        "adsh": adsh,
                        "tag": tag,
                        "ddate": ddate.isoformat(),
                        "qtrs": qtrs,
                        "value": value,
                    }
                )
        return filing_tickers, records

    def prepare_seed(
        self,
        cik_map: dict[str, str],
        tickers: Iterable[str],
        cutoff: str,
        seed_path: str | Path,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        requested = sorted({str(ticker).upper() for ticker in tickers})
        seed_path = Path(seed_path)
        if seed_path.exists() and not force:
            try:
                existing = json.loads(seed_path.read_text(encoding="utf-8"))
                if (
                    existing.get("metadata", {}).get("cutoff") == cutoff
                    and sorted(existing.get("metadata", {}).get("requested_tickers", [])) == requested
                ):
                    status = dict(existing.get("status") or {})
                    status["cache_hit"] = True
                    self._write_status(status)
                    print(
                        f"[SEC-FSDS] seed cache hit available={status.get('available', 0)} "
                        f"eligible={status.get('historically_eligible_count', 0)}",
                        flush=True,
                    )
                    return status
            except (OSError, ValueError, TypeError):
                pass

        cutoff_date = dt.date.fromisoformat(cutoff)
        ticker_to_cik = {
            ticker: _normalize_cik(cik_map[ticker])
            for ticker in requested
            if ticker in cik_map
        }
        missing_cik = sorted(set(requested) - set(ticker_to_cik))
        cik_to_ticker = {cik: ticker for ticker, cik in ticker_to_cik.items()}
        all_records: list[dict[str, Any]] = []
        filing_tickers: set[str] = set()
        downloaded: list[str] = []
        download_errors: dict[str, str] = {}

        for index, period in enumerate(self.periods, 1):
            try:
                archive = self._download(period, force=force)
                eligible, records = self._extract_records(archive, cik_to_ticker, cutoff_date)
                filing_tickers.update(eligible)
                all_records.extend(records)
                downloaded.append(period)
                print(
                    f"[SEC-FSDS] parsed {index}/{len(self.periods)} period={period} "
                    f"records={len(records)} cumulative={len(all_records)}",
                    flush=True,
                )
            except (SecFsdsError, OSError, zipfile.BadZipFile) as exc:
                download_errors[period] = str(exc)
                print(f"[SEC-FSDS] period={period} failed: {exc}", flush=True)
                break

        if download_errors or len(downloaded) != len(self.periods):
            status = {
                "status": "DOWNLOAD_FAILED",
                "provider": "sec_financial_statement_data_sets",
                "cutoff": cutoff,
                "periods_required": list(self.periods),
                "periods_downloaded": downloaded,
                "requested": len(requested),
                "available": 0,
                "missing_cik": missing_cik,
                "errors": download_errors,
                "cache_hit": False,
            }
            self._write_status(status)
            seed_path.unlink(missing_ok=True)
            return status

        by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in all_records:
            by_ticker[str(record["ticker"])].append(record)

        series: dict[str, dict[str, list[list[Any]]]] = {metric: {} for metric in METRICS}
        tags_used: dict[str, dict[str, str | None]] = {metric: {} for metric in METRICS}
        series_audit: dict[str, dict[str, Any]] = {metric: {} for metric in METRICS}
        available_tickers: set[str] = set()
        company_status: dict[str, Any] = {}
        for ticker in requested:
            ticker_records = by_ticker.get(ticker, [])
            metric_lengths: dict[str, int] = {}
            for metric in METRICS:
                tag, values, audit = build_quarterly_series_detailed(
                    ticker_records, FLOW_TAGS[metric], metric
                )
                series[metric][ticker] = [[date, value] for date, value in values]
                tags_used[metric][ticker] = tag
                series_audit[metric][ticker] = audit
                metric_lengths[metric] = len(values)
            usable = metric_lengths.get("revenue", 0) >= 5 or metric_lengths.get("capex", 0) >= 5
            if usable:
                available_tickers.add(ticker)
            company_status[ticker] = {
                "historically_eligible": ticker in filing_tickers,
                "usable": usable,
                "metric_lengths": metric_lengths,
            }

        eligible_count = len(filing_tickers)
        available_count = len(available_tickers)
        coverage = available_count / eligible_count if eligible_count else 0.0
        status = {
            "status": "READY" if coverage >= 0.75 and available_count >= 20 else "INSUFFICIENT_COVERAGE",
            "provider": "sec_financial_statement_data_sets",
            "cutoff": cutoff,
            "periods_required": list(self.periods),
            "periods_downloaded": downloaded,
            "requested": len(requested),
            "historically_eligible_count": eligible_count,
            "historically_eligible": sorted(filing_tickers),
            "historically_ineligible_or_no_filing": sorted(set(requested) - filing_tickers),
            "available": available_count,
            "available_tickers": sorted(available_tickers),
            "coverage_of_historically_eligible": round(coverage, 4),
            "missing_cik": missing_cik,
            "company_status": company_status,
            "errors": {},
            "cache_hit": False,
        }
        seed = {
            "metadata": {
                "version": "0.8.6",
                "source": "U.S. SEC Financial Statement Data Sets",
                "source_url_template": BASE_URL,
                "periods": list(self.periods),
                "cutoff": cutoff,
                "requested_tickers": requested,
                "point_in_time_rule": "filed and statement period must be on or before cutoff",
            },
            "status": status,
            "series": series,
            "tags_used": tags_used,
            "series_audit": series_audit,
        }
        seed_path.parent.mkdir(parents=True, exist_ok=True)
        seed_path.write_text(json.dumps(seed, ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_status(status)
        print(
            f"[SEC-FSDS] seed ready status={status['status']} eligible={eligible_count} "
            f"available={available_count} coverage={coverage:.1%}",
            flush=True,
        )
        return status

    def load_seed(
        self,
        seed_path: str | Path,
        tickers: Iterable[str],
    ) -> tuple[dict[str, dict[str, list[tuple[str, float]]]], dict[str, Any], dict[str, str]]:
        seed_path = Path(seed_path)
        requested = sorted({str(ticker).upper() for ticker in tickers})
        empty = {metric: {ticker: [] for ticker in requested} for metric in METRICS}
        if not seed_path.exists():
            status = self._read_status() or {
                "status": "SEED_MISSING",
                "provider": "sec_financial_statement_data_sets",
                "requested": len(requested),
                "available": 0,
            }
            return empty, status, {ticker: "SEC FSDS seed unavailable" for ticker in requested}
        try:
            payload = json.loads(seed_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            status = {
                "status": "SEED_INVALID",
                "provider": "sec_financial_statement_data_sets",
                "requested": len(requested),
                "available": 0,
                "error": str(exc),
            }
            return empty, status, {ticker: "SEC FSDS seed invalid" for ticker in requested}

        raw_series = payload.get("series") or {}
        series: dict[str, dict[str, list[tuple[str, float]]]] = {metric: {} for metric in METRICS}
        errors: dict[str, str] = {}
        for metric in METRICS:
            metric_payload = raw_series.get(metric) or {}
            for ticker in requested:
                values: list[tuple[str, float]] = []
                for row in metric_payload.get(ticker, []):
                    try:
                        values.append((str(row[0]), float(row[1])))
                    except (IndexError, TypeError, ValueError):
                        continue
                series[metric][ticker] = values
            
        status = dict(payload.get("status") or {})
        for ticker in requested:
            company = (status.get("company_status") or {}).get(ticker) or {}
            if not company.get("usable"):
                errors[ticker] = "fewer than five quarterly revenue/capex observations as of cutoff"
        return series, status, errors

    def _write_status(self, status: dict[str, Any]) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_status(self) -> dict[str, Any] | None:
        try:
            return json.loads(self.status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
