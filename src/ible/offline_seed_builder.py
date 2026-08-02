from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from ible.analytics.sec_metrics import FLOW_TAGS
from ible.collectors.sec_fsds import build_quarterly_series_detailed

FORMS = {"10-K", "10-Q", "20-F", "40-F"}
METRICS = ("capex", "rd", "revenue", "gross_profit", "operating_income")
ARXIV_URL = "https://export.arxiv.org/api/query"
ARXIV_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
}


class OfflineSeedError(RuntimeError):
    pass


def _iso_date(value: str) -> dt.date:
    value = str(value or "").strip().replace("-", "")
    if len(value) != 8 or not value.isdigit():
        raise ValueError(f"invalid date: {value!r}")
    return dt.date(int(value[:4]), int(value[4:6]), int(value[6:8]))


def _float(value: str) -> float:
    number = float(str(value).strip())
    if not math.isfinite(number):
        raise ValueError("non-finite number")
    return number


def _normalize_cik(value: str | int) -> str:
    return str(int(str(value).strip())).zfill(10)


def _reader(zf: zipfile.ZipFile, filename: str) -> Iterable[dict[str, str]]:
    names = {name.lower(): name for name in zf.namelist()}
    actual = names.get(filename.lower())
    if not actual:
        raise OfflineSeedError(f"{filename} missing from SEC archive")
    raw = zf.open(actual, "r")
    text = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="")
    return csv.DictReader(text, delimiter="\t")


def build_quarterly_series(
    records: list[dict[str, Any]],
    tags: list[str],
    metric: str | None = None,
) -> tuple[str | None, list[tuple[str, float]]]:
    tag, values, _ = build_quarterly_series_detailed(records, tags, metric)
    return tag, values


def _valid_archive(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 1_000_000:
        return False
    try:
        with zipfile.ZipFile(path) as zf:
            names = {name.lower() for name in zf.namelist()}
            return {"sub.txt", "num.txt"}.issubset(names)
    except (OSError, zipfile.BadZipFile):
        return False


def _safe_body(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read(600).decode("utf-8", errors="replace").replace("\n", " ")
    except Exception:
        return ""


def download_archive(url: str, destination: Path, user_agent: str, retries: int = 3) -> Path:
    if _valid_archive(destination):
        print(f"[LOCAL-SEC] cache hit {destination.name} size={destination.stat().st_size:,}", flush=True)
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_suffix(destination.suffix + ".part")
    last_error = "unknown error"
    waits = [0, 20, 65]
    for attempt in range(retries):
        if waits[attempt]:
            print(f"[LOCAL-SEC] retry wait={waits[attempt]}s", flush=True)
            time.sleep(waits[attempt])
        try:
            headers = {
                "User-Agent": user_agent,
                "Accept": "application/zip,application/octet-stream;q=0.9,*/*;q=0.1",
                "Accept-Encoding": "identity",
                "Connection": "close",
            }
            request = urllib.request.Request(url, headers=headers)
            part.unlink(missing_ok=True)
            with urllib.request.urlopen(request, timeout=300) as response, part.open("wb") as fh:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise OfflineSeedError(f"HTTP {status}")
                expected = int(response.headers.get("Content-Length") or 0)
                received = 0
                next_log = 20 * 1024 * 1024
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    received += len(chunk)
                    if received >= next_log:
                        print(f"[LOCAL-SEC] {destination.name} {received // (1024 * 1024)}MB", flush=True)
                        next_log += 20 * 1024 * 1024
                if expected and received != expected:
                    raise OfflineSeedError(f"truncated expected={expected} received={received}")
            if not _valid_archive(part):
                raise OfflineSeedError("downloaded file is not a valid SEC FSDS ZIP")
            part.replace(destination)
            print(f"[LOCAL-SEC] ready {destination.name} size={destination.stat().st_size:,}", flush=True)
            return destination
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code} body={_safe_body(exc)}"
        except (urllib.error.URLError, TimeoutError, OSError, OfflineSeedError) as exc:
            last_error = str(exc)
        finally:
            if part.exists() and not _valid_archive(part):
                part.unlink(missing_ok=True)
        print(f"[LOCAL-SEC] failed {destination.name} attempt={attempt + 1}/{retries}: {last_error}", flush=True)
    raise OfflineSeedError(
        f"{destination.name} download failed: {last_error}. "
        f"Open this URL in a browser and save it as {destination}: {url}"
    )


def extract_records(
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


def _date_token(value: dt.date, end_of_day: bool = False) -> str:
    return value.strftime("%Y%m%d") + ("2359" if end_of_day else "0000")


def _arxiv_count(query: str, start: dt.date, end: dt.date, user_agent: str) -> int:
    date_filter = f"submittedDate:[{_date_token(start)} TO {_date_token(end, True)}]"
    search_query = f"({query}) AND {date_filter}"
    params = urllib.parse.urlencode(
        {
            "search_query": search_query,
            "start": 0,
            "max_results": 1,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
    )
    request = urllib.request.Request(
        f"{ARXIV_URL}?{params}",
        headers={"User-Agent": user_agent, "Accept": "application/atom+xml"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        text = response.read().decode("utf-8", errors="replace")
    root = ET.fromstring(text)
    node = root.find("opensearch:totalResults", ARXIV_NS)
    if node is None or node.text is None:
        raise OfflineSeedError("arXiv response missing totalResults")
    return int(node.text.strip())


def collect_research(query: str, as_of: dt.date, user_agent: str) -> dict[str, Any]:
    windows: list[tuple[str, dt.date, dt.date]] = []
    cursor_end = as_of
    for label in ("recent", "prior", "older"):
        cursor_start = cursor_end - dt.timedelta(days=364)
        windows.append((label, cursor_start, cursor_end))
        cursor_end = cursor_start - dt.timedelta(days=1)
    counts: dict[str, int] = {}
    for label, start, end in windows:
        last_error = "unknown"
        for attempt in range(2):
            try:
                counts[label] = _arxiv_count(query, start, end, user_agent)
                break
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ET.ParseError, OfflineSeedError) as exc:
                last_error = str(exc)
                if attempt == 0:
                    time.sleep(8)
        else:
            raise OfflineSeedError(last_error)
        time.sleep(3.2)
    return {
        "query": query,
        "as_of": as_of.isoformat(),
        "windows": {
            label: {"start": start.isoformat(), "end": end.isoformat(), "count": counts[label]}
            for label, start, end in windows
        },
        "counts": counts,
    }


def _integrity_payload(seed: dict[str, Any]) -> dict[str, Any]:
    return {
        "series": seed.get("series") or {},
        "tags_used": seed.get("tags_used") or {},
        "series_audit": seed.get("series_audit") or {},
        "research": seed.get("research") or {},
        "status": {
            key: (seed.get("status") or {}).get(key)
            for key in (
                "cutoff",
                "periods_required",
                "periods_downloaded",
                "requested",
                "historically_eligible_count",
                "historically_eligible",
                "available",
                "available_tickers",
                "coverage_of_historically_eligible",
                "research_required",
                "research_available",
            )
        },
    }


def compute_seed_sha256(seed: dict[str, Any]) -> str:
    raw = json.dumps(_integrity_payload(seed), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_request(root: Path) -> dict[str, Any]:
    path = root / "config" / "offline_seed_request.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OfflineSeedError(f"cannot read {path}: {exc}") from exc
    if payload.get("generator_version") != "0.8.8":
        raise OfflineSeedError("offline_seed_request.json is not version 0.8.8")
    return payload


def build_seed(root: Path, user_agent: str, *, refresh: bool = False, skip_research: bool = False) -> dict[str, Any]:
    request = load_request(root)
    if "@" not in user_agent or len(user_agent) < 12:
        raise OfflineSeedError("User-Agent must include an application name and real contact email")

    cutoff = dt.date.fromisoformat(str(request["cutoff"]))
    periods = [str(value) for value in request["periods"]]
    requested = [str(row["ticker"]).upper() for row in request["tickers"]]
    ticker_to_cik = {str(row["ticker"]).upper(): _normalize_cik(row["cik"]) for row in request["tickers"]}
    cik_to_ticker = {cik: ticker for ticker, cik in ticker_to_cik.items()}
    download_dir = root / "local_sec_data"
    source_template = str(request["source_url_template"])

    archives: list[Path] = []
    for index, period in enumerate(periods, 1):
        destination = download_dir / f"{period}.zip"
        if refresh:
            destination.unlink(missing_ok=True)
        print(f"[LOCAL-SEC] download {index}/{len(periods)} period={period}", flush=True)
        archives.append(download_archive(source_template.format(period=period), destination, user_agent))
        time.sleep(2)

    all_records: list[dict[str, Any]] = []
    filing_tickers: set[str] = set()
    for index, (period, archive) in enumerate(zip(periods, archives), 1):
        eligible, records = extract_records(archive, cik_to_ticker, cutoff)
        filing_tickers.update(eligible)
        all_records.extend(records)
        print(
            f"[LOCAL-SEC] parsed {index}/{len(periods)} period={period} records={len(records):,} cumulative={len(all_records):,}",
            flush=True,
        )

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

    research: dict[str, Any] = {}
    research_errors: dict[str, str] = {}
    research_rows = list(request.get("research") or [])
    if not skip_research:
        for index, row in enumerate(research_rows, 1):
            theme_id = str(row["theme_id"])
            print(f"[LOCAL-ARXIV] {index}/{len(research_rows)} theme={theme_id}", flush=True)
            try:
                research[theme_id] = collect_research(str(row["query"]), cutoff, user_agent)
            except Exception as exc:  # noqa: BLE001
                research_errors[theme_id] = str(exc)
                print(f"[LOCAL-ARXIV] failed theme={theme_id}: {exc}", flush=True)

    eligible_count = len(filing_tickers)
    available_count = len(available_tickers)
    coverage = available_count / eligible_count if eligible_count else 0.0
    minimum_coverage = float(request.get("minimum_financial_coverage", 0.75))
    minimum_available = int(request.get("minimum_available_companies", 20))
    minimum_research = int(request.get("minimum_research_themes", 6))
    financial_ready = coverage >= minimum_coverage and available_count >= minimum_available
    research_ready = len(research) >= minimum_research if not skip_research else False
    status_value = "READY" if financial_ready and research_ready else "INSUFFICIENT_COVERAGE"

    selected_audits = [
        audit.get("selected") or {}
        for metric_rows in series_audit.values()
        for audit in metric_rows.values()
        if isinstance(audit, dict) and audit.get("selected_tag")
    ]
    normalization_summary = {
        "version": "fsds_quarter_v2_single_tag_robust",
        "selected_series": len(selected_audits),
        "mixed_tag_series": 0,
        "series_with_extreme_yoy": sum(
            1 for audit in selected_audits if int(audit.get("extreme_yoy_count") or 0) > 0
        ),
        "rejected_negative_values": sum(
            int(audit.get("rejected_negative") or 0) for audit in selected_audits
        ),
    }

    status = {
        "status": status_value,
        "provider": "offline_sec_financial_statement_data_sets_plus_arxiv",
        "cutoff": cutoff.isoformat(),
        "periods_required": periods,
        "periods_downloaded": periods,
        "requested": len(requested),
        "historically_eligible_count": eligible_count,
        "historically_eligible": sorted(filing_tickers),
        "historically_ineligible_or_no_filing": sorted(set(requested) - filing_tickers),
        "available": available_count,
        "available_tickers": sorted(available_tickers),
        "coverage_of_historically_eligible": round(coverage, 4),
        "missing_cik": [],
        "company_status": company_status,
        "research_required": len(research_rows),
        "research_available": len(research),
        "research_errors": research_errors,
        "normalization": normalization_summary,
        "cache_hit": False,
    }
    seed: dict[str, Any] = {
        "metadata": {
            "schema_version": 3,
            "version": "0.8.8",
            "normalization_version": "fsds_quarter_v2_single_tag_robust",
            "source": "U.S. SEC Financial Statement Data Sets + arXiv Atom API",
            "source_url_template": source_template,
            "periods": periods,
            "cutoff": cutoff.isoformat(),
            "requested_tickers": requested,
            "point_in_time_rule": "filed and statement period must be on or before cutoff",
            "generated_locally": True,
            "generated_at_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        },
        "status": status,
        "series": series,
        "tags_used": tags_used,
        "series_audit": series_audit,
        "research": research,
    }
    seed["metadata"]["content_sha256"] = compute_seed_sha256(seed)
    seed_path = root / "validation_seed" / "sec_fsds_fy2021.json"
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed_path.write_text(json.dumps(seed, ensure_ascii=False, indent=2), encoding="utf-8")

    upload_dir = root / "UPLOAD_THIS_FOLDER_TO_GITHUB" / "validation_seed"
    upload_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(seed_path, upload_dir / seed_path.name)

    print(
        f"[OFFLINE-SEED] status={status_value} eligible={eligible_count} available={available_count} "
        f"coverage={coverage:.1%} research={len(research)}/{len(research_rows)}",
        flush=True,
    )
    print(f"[OFFLINE-SEED] file={seed_path}", flush=True)
    print(f"[OFFLINE-SEED] upload_folder={upload_dir.parent}", flush=True)
    if status_value != "READY":
        raise OfflineSeedError(
            "Seed did not pass completeness gate. Re-run after failed downloads/queries are available. "
            f"financial={available_count}/{eligible_count}, research={len(research)}/{len(research_rows)}"
        )
    return seed


def validate_seed(root: Path) -> dict[str, Any]:
    request = load_request(root)
    path = root / "validation_seed" / "sec_fsds_fy2021.json"
    if not path.exists():
        raise OfflineSeedError(
            "validation_seed/sec_fsds_fy2021.json is missing. Run 1_BUILD_OFFLINE_SEED.bat on a Windows PC first."
        )
    try:
        seed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OfflineSeedError(f"seed JSON is invalid: {exc}") from exc
    metadata = seed.get("metadata") or {}
    status = seed.get("status") or {}
    expected_tickers = sorted(str(row["ticker"]).upper() for row in request["tickers"])
    errors: list[str] = []
    if metadata.get("version") != "0.8.8" or metadata.get("schema_version") != 3:
        errors.append("seed version/schema mismatch")
    if metadata.get("normalization_version") != "fsds_quarter_v2_single_tag_robust":
        errors.append("seed normalization version mismatch")
    if metadata.get("cutoff") != request.get("cutoff"):
        errors.append("cutoff mismatch")
    if sorted(metadata.get("requested_tickers") or []) != expected_tickers:
        errors.append("requested ticker cohort mismatch")
    if status.get("status") != "READY":
        errors.append(f"status is {status.get('status')}")
    if list(status.get("periods_downloaded") or []) != list(request.get("periods") or []):
        errors.append("required SEC quarters are incomplete")
    if float(status.get("coverage_of_historically_eligible") or 0) < float(request["minimum_financial_coverage"]):
        errors.append("financial coverage below minimum")
    if int(status.get("available") or 0) < int(request["minimum_available_companies"]):
        errors.append("available company count below minimum")
    if int(status.get("research_available") or 0) < int(request["minimum_research_themes"]):
        errors.append("research theme coverage below minimum")
    expected_hash = str(metadata.get("content_sha256") or "")
    actual_hash = compute_seed_sha256(seed)
    if not expected_hash or expected_hash != actual_hash:
        errors.append("seed integrity SHA-256 mismatch")
    if errors:
        raise OfflineSeedError("; ".join(errors))
    result = {
        "status": "READY",
        "version": metadata["version"],
        "content_sha256": actual_hash,
        "available": status.get("available"),
        "historically_eligible_count": status.get("historically_eligible_count"),
        "coverage": status.get("coverage_of_historically_eligible"),
        "research_available": status.get("research_available"),
        "research_required": status.get("research_required"),
        "normalization": status.get("normalization"),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or validate the V0.8.8 offline SEC/arXiv seed")
    parser.add_argument("--root", default=".")
    parser.add_argument("--email", default="")
    parser.add_argument("--user-agent", default="")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--skip-research", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    try:
        if args.validate_only:
            validate_seed(root)
            return 0
        user_agent = args.user_agent.strip() or f"IndustryBoomLeadingEngine/0.8.8 {args.email.strip()}"
        build_seed(root, user_agent, refresh=args.refresh, skip_research=args.skip_research)
        return 0
    except OfflineSeedError as exc:
        print(f"[OFFLINE-SEED-ERROR] {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
