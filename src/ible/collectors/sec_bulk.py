from __future__ import annotations

import json
import os
import time
import zipfile
from pathlib import Path
from typing import Any, Iterable

import requests


class SecBulkError(RuntimeError):
    pass


class SecBulkClient:
    """Download SEC's nightly bulk Company Facts archive and extract a small subset.

    The SEC documents the bulk archive as the efficient route for large XBRL pulls.
    A repository cache should retain only the extracted CIK JSON files, not the full
    archive, so subsequent historical validations are fast and small.
    """

    ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"

    def __init__(
        self,
        cache_dir: str | Path,
        user_agent: str,
        timeout: int = 180,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.user_agent = user_agent.strip() or "IndustryBoomLeadingEngine/0.8 contact@example.com"
        self.timeout = timeout
        self.archive_path = self.cache_dir / "companyfacts.zip"
        self.subset_dir = self.cache_dir / "subset"

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept": "application/zip,application/octet-stream,*/*",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }

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
                with requests.get(
                    self.ARCHIVE_URL,
                    headers=self._headers(),
                    stream=True,
                    timeout=(20, self.timeout),
                ) as response:
                    response.raise_for_status()
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

    def prepare_subset(
        self,
        ticker_to_cik: dict[str, str],
        tickers: Iterable[str],
        force_archive: bool = False,
    ) -> dict[str, Any]:
        requested = sorted({str(ticker).upper() for ticker in tickers})
        missing_map = [ticker for ticker in requested if ticker not in ticker_to_cik]
        if missing_map:
            raise SecBulkError(f"tickers missing from SEC CIK map: {missing_map}")
        self.subset_dir.mkdir(parents=True, exist_ok=True)

        cached = {
            ticker: self.subset_dir / f"{ticker}.json"
            for ticker in requested
            if (self.subset_dir / f"{ticker}.json").exists()
        }
        if len(cached) == len(requested) and not force_archive:
            return {
                "status": "CACHE_HIT",
                "requested": len(requested),
                "extracted": len(cached),
                "missing": [],
                "subset_dir": str(self.subset_dir),
            }

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
                    json.dumps(payload, ensure_ascii=False), encoding="utf-8"
                )
                extracted += 1
                if index == len(requested) or index % 10 == 0:
                    print(
                        f"[SEC-BULK] subset {index}/{len(requested)} extracted={extracted} missing={len(missing)}",
                        flush=True,
                    )

        # The full nightly archive is intentionally deleted after extraction to keep
        # GitHub cache size manageable. The small subset remains cached.
        if not os.getenv("SEC_KEEP_FULL_ARCHIVE", "").lower() in {"1", "true", "yes"}:
            if archive == self.archive_path:
                archive.unlink(missing_ok=True)

        return {
            "status": "EXTRACTED",
            "requested": len(requested),
            "extracted": extracted,
            "missing": missing,
            "subset_dir": str(self.subset_dir),
        }

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
