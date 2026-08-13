from __future__ import annotations

import json
import os
import re
import time
import zipfile
from pathlib import Path
from typing import Any, Iterable

import requests


class SecBulkError(RuntimeError):
    pass


class SecBulkClient:
    """Prepare a small SEC Company Facts subset for the configured tickers.

    GitHub-hosted runners can receive HTTP 403 responses when downloading the
    very large ``companyfacts.zip`` archive from ``www.sec.gov``.  The default
    path therefore uses SEC's official per-company Company Facts API on
    ``data.sec.gov`` and falls back to the nightly bulk archive only when
    explicitly requested or when the API path leaves missing companies.

    The resulting JSON files have the same schema as members of the official
    bulk archive, so downstream calculations are source-agnostic.
    """

    ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
    API_URL_TEMPLATE = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    VALID_MODES = {"auto", "api", "bulk"}

    def __init__(
        self,
        cache_dir: str | Path,
        user_agent: str,
        timeout: int = 180,
        min_interval: float = 0.35,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.user_agent = user_agent.strip()
        self.timeout = timeout
        self.min_interval = max(0.11, float(min_interval))
        self.archive_path = self.cache_dir / "companyfacts.zip"
        self.subset_dir = self.cache_dir / "subset"
        self.status_path = self.cache_dir / "sec_download_status.json"
        self._last_request_at = 0.0

    @staticmethod
    def validate_user_agent(value: str) -> None:
        text = value.strip()
        email = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.IGNORECASE)
        if len(text) < 12 or email is None:
            raise SecBulkError(
                "SEC_USER_AGENT must contain an organization/name and a real contact email, "
                "for example: MyResearchName admin@example.com"
            )

    def _headers(self, *, json_response: bool = False) -> dict[str, str]:
        self.validate_user_agent(self.user_agent)
        return {
            "User-Agent": self.user_agent,
            "Accept": "application/json" if json_response else "application/zip,application/octet-stream,*/*",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }

    def _respect_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request_at = time.monotonic()

    @staticmethod
    def _response_detail(response: requests.Response) -> str:
        content_type = response.headers.get("content-type", "")
        request_id = response.headers.get("x-amzn-requestid") or response.headers.get("x-request-id")
        detail = f"HTTP {response.status_code} content-type={content_type or 'unknown'}"
        if request_id:
            detail += f" request-id={request_id}"
        try:
            body = response.text[:240].replace("\n", " ").strip()
        except Exception:  # noqa: BLE001
            body = ""
        if body:
            detail += f" body={body}"
        return detail

    def _write_status(self, payload: dict[str, Any]) -> None:
        self.status_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def ensure_archive(self, force: bool = False) -> Path:
        override = os.getenv("SEC_COMPANYFACTS_ZIP_PATH", "").strip()
        if override:
            path = Path(override)
            if not path.exists():
                raise SecBulkError(f"SEC_COMPANYFACTS_ZIP_PATH does not exist: {path}")
            return path
        if self.archive_path.exists() and self.archive_path.stat().st_size > 1_000_000 and not force:
            return self.archive_path

        temp_path = self.archive_path.with_suffix(".zip.part")
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                self._respect_rate_limit()
                with requests.get(
                    self.ARCHIVE_URL,
                    headers=self._headers(),
                    stream=True,
                    timeout=(20, self.timeout),
                ) as response:
                    if response.status_code >= 400:
                        raise SecBulkError(self._response_detail(response))
                    total = int(response.headers.get("content-length") or 0)
                    downloaded = 0
                    next_log = 100 * 1024 * 1024
                    with temp_path.open("wb") as handle:
                        for chunk in response.iter_content(chunk_size=4 * 1024 * 1024):
                            if not chunk:
                                continue
                            handle.write(chunk)
                            downloaded += len(chunk)
                            if downloaded >= next_log:
                                total_text = f"/{total / 1024 / 1024:.0f}MB" if total else ""
                                print(
                                    f"[SEC-BULK] downloaded {downloaded / 1024 / 1024:.0f}MB{total_text}",
                                    flush=True,
                                )
                                next_log += 100 * 1024 * 1024
                if temp_path.stat().st_size < 1_000_000:
                    raise SecBulkError("SEC bulk archive is unexpectedly small")
                if not zipfile.is_zipfile(temp_path):
                    raise SecBulkError("SEC bulk response is not a valid ZIP archive")
                temp_path.replace(self.archive_path)
                return self.archive_path
            except Exception as exc:  # noqa: BLE001 - preserve network context
                last_error = exc
                temp_path.unlink(missing_ok=True)
                if attempt < 3:
                    time.sleep(2**attempt)
        raise SecBulkError(f"SEC bulk download failed after 3 attempts: {last_error}")

    @staticmethod
    def _normalize_cik(value: str | int) -> str:
        digits = "".join(ch for ch in str(value) if ch.isdigit())
        if not digits:
            raise ValueError(f"invalid CIK: {value}")
        return f"{int(digits):010d}"

    def _download_companyfacts_api(
        self,
        ticker_to_cik: dict[str, str],
        tickers: Iterable[str],
    ) -> tuple[int, dict[str, str]]:
        requested = sorted({str(ticker).upper() for ticker in tickers})
        errors: dict[str, str] = {}
        downloaded = 0
        self.subset_dir.mkdir(parents=True, exist_ok=True)
        session = requests.Session()

        for index, ticker in enumerate(requested, start=1):
            cik = self._normalize_cik(ticker_to_cik[ticker])
            url = self.API_URL_TEMPLATE.format(cik=cik)
            last_error = "unknown error"
            for attempt in range(1, 4):
                try:
                    self._respect_rate_limit()
                    response = session.get(
                        url,
                        headers=self._headers(json_response=True),
                        timeout=(20, min(self.timeout, 120)),
                    )
                    if response.status_code >= 400:
                        raise SecBulkError(self._response_detail(response))
                    payload = response.json()
                    if not isinstance(payload, dict) or not isinstance(payload.get("facts"), dict):
                        raise SecBulkError("response is not a valid SEC Company Facts object")
                    (self.subset_dir / f"{ticker}.json").write_text(
                        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                        encoding="utf-8",
                    )
                    downloaded += 1
                    last_error = ""
                    break
                except Exception as exc:  # noqa: BLE001
                    last_error = str(exc)
                    # 403 = runner IP blocked; retrying the same IP won't help.
                    # Break immediately on second 403 so the stage stays within timeout.
                    if "HTTP 403" in last_error:
                        if attempt >= 2:
                            break
                        time.sleep(1)
                    elif attempt < 3:
                        time.sleep(2**attempt)
            if last_error:
                errors[ticker] = last_error
            if index == len(requested) or index % 5 == 0:
                print(
                    f"[SEC-API] {index}/{len(requested)} downloaded={downloaded} errors={len(errors)}",
                    flush=True,
                )
        return downloaded, errors

    def _extract_from_archive(
        self,
        ticker_to_cik: dict[str, str],
        tickers: Iterable[str],
        force_archive: bool,
    ) -> tuple[int, list[str]]:
        requested = sorted({str(ticker).upper() for ticker in tickers})
        archive = self.ensure_archive(force=force_archive)
        member_lookup: dict[str, str] = {}
        with zipfile.ZipFile(archive) as zf:
            for member in zf.namelist():
                base = Path(member).name
                if base.startswith("CIK") and base.endswith(".json"):
                    member_lookup[base] = member

            extracted = 0
            missing: list[str] = []
            for index, ticker in enumerate(requested, start=1):
                cik = self._normalize_cik(ticker_to_cik[ticker])
                member = member_lookup.get(f"CIK{cik}.json")
                if not member:
                    missing.append(ticker)
                    continue
                payload = json.loads(zf.read(member))
                (self.subset_dir / f"{ticker}.json").write_text(
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
                )
                extracted += 1
                if index == len(requested) or index % 10 == 0:
                    print(
                        f"[SEC-BULK] subset {index}/{len(requested)} extracted={extracted} missing={len(missing)}",
                        flush=True,
                    )

        if os.getenv("SEC_KEEP_FULL_ARCHIVE", "").lower() not in {"1", "true", "yes"}:
            if archive == self.archive_path:
                archive.unlink(missing_ok=True)
        return extracted, missing

    def prepare_subset(
        self,
        ticker_to_cik: dict[str, str],
        tickers: Iterable[str],
        force_archive: bool = False,
        source_mode: str | None = None,
    ) -> dict[str, Any]:
        requested = sorted({str(ticker).upper() for ticker in tickers})
        missing_map = [ticker for ticker in requested if ticker not in ticker_to_cik]
        if missing_map:
            raise SecBulkError(f"tickers missing from SEC CIK map: {missing_map}")
        self.validate_user_agent(self.user_agent)
        self.subset_dir.mkdir(parents=True, exist_ok=True)

        configured_mode = source_mode or os.getenv("SEC_SOURCE_MODE", "auto")
        if os.getenv("SEC_COMPANYFACTS_ZIP_PATH", "").strip() and source_mode is None:
            configured_mode = "bulk"
        mode = configured_mode.strip().lower()
        if mode not in self.VALID_MODES:
            raise SecBulkError(f"invalid SEC source mode: {mode}; expected one of {sorted(self.VALID_MODES)}")

        if force_archive:
            for ticker in requested:
                (self.subset_dir / f"{ticker}.json").unlink(missing_ok=True)

        existing = [ticker for ticker in requested if (self.subset_dir / f"{ticker}.json").exists()]
        if len(existing) == len(requested) and not force_archive:
            result = {
                "status": "CACHE_HIT",
                "source_mode": mode,
                "requested": len(requested),
                "extracted": len(existing),
                "missing": [],
                "subset_dir": str(self.subset_dir),
            }
            self._write_status(result)
            return result

        remaining = [ticker for ticker in requested if not (self.subset_dir / f"{ticker}.json").exists()]
        api_errors: dict[str, str] = {}
        bulk_error: str | None = None
        api_downloaded = 0
        bulk_extracted = 0

        # API first is deliberate: GitHub shared runners often receive 403 for
        # the multi-hundred-MB archive while the official data API remains usable.
        if mode in {"auto", "api"} and remaining:
            print(f"[SEC] source=companyfacts_api companies={len(remaining)}", flush=True)
            api_downloaded, api_errors = self._download_companyfacts_api(ticker_to_cik, remaining)
            remaining = [ticker for ticker in requested if not (self.subset_dir / f"{ticker}.json").exists()]

        # Skip bulk download when ALL API calls got 403: same runner IP is blocked
        # for both API and bulk archive — attempting the 3 GB download just wastes time.
        all_403 = bool(api_errors) and all("HTTP 403" in err for err in api_errors.values())
        if mode in {"auto", "bulk"} and remaining and not all_403:
            try:
                print(f"[SEC] source=nightly_bulk fallback_companies={len(remaining)}", flush=True)
                bulk_extracted, _ = self._extract_from_archive(
                    ticker_to_cik,
                    remaining,
                    force_archive=force_archive,
                )
            except Exception as exc:  # noqa: BLE001
                bulk_error = str(exc)
                print(f"[SEC] bulk fallback unavailable: {bulk_error}", flush=True)
        elif all_403 and remaining:
            bulk_error = "skipped: all API calls returned HTTP 403 (runner IP blocked); bulk archive uses the same IP"
            print(f"[SEC] bulk skipped — runner IP blocked (all 403)", flush=True)

        present = [ticker for ticker in requested if (self.subset_dir / f"{ticker}.json").exists()]
        missing = [ticker for ticker in requested if ticker not in present]
        status = (
            "API_EXTRACTED"
            if api_downloaded and not bulk_extracted and not missing
            else "BULK_EXTRACTED"
            if bulk_extracted and not api_downloaded and not missing
            else "MIXED_EXTRACTED"
            if not missing
            else "PARTIAL"
            if present
            else "SEC_UNAVAILABLE"
        )
        result = {
            "status": status,
            "source_mode": mode,
            "requested": len(requested),
            "extracted": len(present),
            "api_downloaded": api_downloaded,
            "bulk_extracted": bulk_extracted,
            "missing": missing,
            "api_errors": {ticker: api_errors[ticker] for ticker in missing if ticker in api_errors},
            "bulk_error": bulk_error,
            "subset_dir": str(self.subset_dir),
        }
        self._write_status(result)
        if not present:
            raise SecBulkError(
                "SEC data unavailable from both official routes. "
                f"API errors={len(api_errors)}; bulk_error={bulk_error or 'not attempted'}. "
                "This is a network/IP access failure, not a deleted workflow-run problem."
            )
        return result

    def load_subset(self, tickers: Iterable[str]) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
        facts: dict[str, dict[str, Any]] = {}
        errors: dict[str, str] = {}
        for ticker in sorted({str(t).upper() for t in tickers}):
            path = self.subset_dir / f"{ticker}.json"
            if not path.exists():
                errors[ticker] = "subset JSON missing"
                continue
            try:
                facts[ticker] = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                errors[ticker] = str(exc)
        return facts, errors
