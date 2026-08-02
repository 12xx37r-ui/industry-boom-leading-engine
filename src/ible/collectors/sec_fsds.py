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
    """Keep one point-in-time fact per end-date/qtrs pair for one XBRL tag.

    SEC FSDS contains the same comparative fact in later filings.  The latest filing
    available by the configured cutoff is the correct point-in-time restatement, but
    facts from different tags must never be differenced against each other.
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
        # Latest available restatement wins.  If filing dates tie, prefer the filing
        # whose form naturally matches the duration (10-K for qtrs=4, 10-Q otherwise).
        preferred_form = "10-K" if key[1] == 4 else "10-Q"
        if new_filed > old_filed or (
            new_filed == old_filed and new_form == preferred_form and old_form != preferred_form
        ):
            selected[key] = record
    return sorted(selected.values(), key=lambda row: (str(row["ddate"]), int(row["qtrs"]), str(row.get("filed") or "")))


def _series_for_tag(
    records: list[dict[str, Any]],
    tag: str,
    metric: str | None,
) -> tuple[list[tuple[str, float]], dict[str, Any]]:
    rows = _dedupe_single_tag(records, tag)
    direct: dict[str, tuple[float, str, str]] = {}
    cumulative: dict[int, list[dict[str, Any]]] = defaultdict(list)
    rejected_negative = 0
    derived_count = 0
    nonnegative_metric = metric in {"revenue", "capex", "rd"}

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
            old = direct.get(str(row["ddate"]))
            filed = str(row.get("filed") or "")
            if old is None or filed > old[1]:
                direct[str(row["ddate"])] = (value, filed, "direct")
        elif qtrs in {2, 3, 4}:
            cumulative[qtrs].append(row)

    def nearest_previous(current: dict[str, Any], expected_qtrs: int) -> dict[str, Any] | None:
        current_date = _iso_date(str(current["ddate"]))
        choices: list[tuple[int, str, dict[str, Any]]] = []
        for candidate in cumulative.get(expected_qtrs, []):
            candidate_date = _iso_date(str(candidate["ddate"]))
            gap = (current_date - candidate_date).days
            if 50 <= gap <= 140:
                choices.append((gap, str(candidate.get("filed") or ""), candidate))
        if not choices:
            return None
        # Closest fiscal quarter first; latest restatement breaks ties.
        choices.sort(key=lambda item: (item[0], item[1]), reverse=False)
        best_gap = choices[0][0]
        tied = [item for item in choices if item[0] == best_gap]
        return max(tied, key=lambda item: item[1])[2]

    for qtrs in (2, 3, 4):
        for row in sorted(cumulative.get(qtrs, []), key=lambda item: (str(item["ddate"]), str(item.get("filed") or ""))):
            end = str(row["ddate"])
            if end in direct:
                continue
            prior = nearest_previous(row, qtrs - 1)
            derived: float | None = None
            if prior is not None:
                derived = float(row["value"]) - float(prior["value"])
            elif qtrs == 4:
                annual_end = _iso_date(end)
                prior_dates = [
                    date
                    for date in sorted(direct)
                    if 45 <= (annual_end - _iso_date(date)).days <= 330
                ]
                # Use exactly the three immediately preceding, reasonably spaced quarters.
                prior_dates = prior_dates[-3:]
                if len(prior_dates) == 3:
                    dates = [_iso_date(date) for date in prior_dates] + [annual_end]
                    gaps = [(dates[index] - dates[index - 1]).days for index in range(1, len(dates))]
                    if all(45 <= gap <= 140 for gap in gaps):
                        derived = float(row["value"]) - sum(direct[date][0] for date in prior_dates)
            if derived is not None and accept(derived):
                direct[end] = (derived, str(row.get("filed") or ""), f"derived_qtrs_{qtrs}")
                derived_count += 1

    result = [(date, value) for date, (value, _, _) in sorted(direct.items())]
    result = result[-16:]

    continuity_gaps: list[int] = []
    for index in range(1, len(result)):
        continuity_gaps.append((_iso_date(result[index][0]) - _iso_date(result[index - 1][0])).days)
    continuity = (
        sum(1 for gap in continuity_gaps if 45 <= gap <= 140) / len(continuity_gaps)
        if continuity_gaps else 0.0
    )
    yoy_values: list[float] = []
    for index in range(4, len(result)):
        previous = result[index - 4][1]
        current = result[index][1]
        if previous != 0:
            yoy_values.append((current - previous) / abs(previous))
    extreme_yoy = sum(1 for value in yoy_values if abs(value) > 5.0)
    audit = {
        "tag": tag,
        "input_records": len(rows),
        "quarter_count": len(result),
        "direct_count": sum(1 for _, (_, _, source) in direct.items() if source == "direct"),
        "derived_count": derived_count,
        "rejected_negative": rejected_negative,
        "continuity_ratio": round(continuity, 4),
        "extreme_yoy_count": extreme_yoy,
        "max_abs_yoy": round(max((abs(value) for value in yoy_values), default=0.0), 6),
    }
    return result, audit


def build_quarterly_series_detailed(
    records: list[dict[str, Any]],
    tags: list[str],
    metric: str | None = None,
) -> tuple[str | None, list[tuple[str, float]], dict[str, Any]]:
    """Build a consistent quarterly series without mixing XBRL concepts.

    Each candidate tag is normalized independently.  The tag with the best usable
    quarter coverage, continuity, and lowest anomaly count is selected.  This avoids
    the V0.8.8 failure mode where one quarter used ``Revenues`` and another used a
    different revenue concept before cumulative subtraction.
    """
    metric = metric or _metric_from_tags(tags)
    candidates: dict[str, dict[str, Any]] = {}
    best: tuple[tuple[float, ...], str, list[tuple[str, float]], dict[str, Any]] | None = None
    for priority, tag in enumerate(tags):
        values, audit = _series_for_tag(records, tag, metric)
        candidates[tag] = audit
        score = (
            float(len(values)),
            float(audit["continuity_ratio"]),
            -float(audit["extreme_yoy_count"]),
            -float(audit["rejected_negative"]),
            -float(priority),
        )
        if best is None or score > best[0]:
            best = (score, tag, values, audit)
    if best is None or not best[2]:
        return None, [], {"metric": metric, "selected_tag": None, "candidates": candidates}
    _, tag, values, selected_audit = best
    return tag, values[-12:], {
        "metric": metric,
        "selected_tag": tag,
        "selected": selected_audit,
        "candidates": candidates,
        "mixed_tags": False,
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
