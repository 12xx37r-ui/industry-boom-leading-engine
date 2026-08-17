from __future__ import annotations

import email.utils
import hashlib
import json
import os
import random
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

# Conservative provider pacing. Explicit published limits are kept below their
# ceilings; providers without a published QPS limit use low-burst operational
# pacing only. This affects transport timing, never engine calculations.
_PROVIDER_POLICIES: dict[str, dict[str, float | int]] = {
    "api.gdeltproject.org": {"min_interval": 7.5, "max_concurrent": 1, "max_attempts": 2, "rate_limit_cooldown": 20.0},
    "data.sec.gov": {"min_interval": 0.15, "max_concurrent": 2},
    "www.sec.gov": {"min_interval": 0.15, "max_concurrent": 2},
    "naverapihub.apigw.ntruss.com": {"min_interval": 0.25, "max_concurrent": 1, "max_attempts": 3, "rate_limit_cooldown": 30.0},
    "api.github.com": {"min_interval": 0.80, "max_concurrent": 2},
    "api.openalex.org": {"min_interval": 0.20, "max_concurrent": 2},
    "api.usaspending.gov": {"min_interval": 0.30, "max_concurrent": 2},
    "api.stlouisfed.org": {"min_interval": 0.30, "max_concurrent": 2},
    "apps.bea.gov": {"min_interval": 0.40, "max_concurrent": 1},
    "opendart.fss.or.kr": {"min_interval": 0.35, "max_concurrent": 1},
    "export.arxiv.org": {"min_interval": 3.5, "max_concurrent": 1},
    "search.patentsview.org": {"min_interval": 0.50, "max_concurrent": 1},
    "patents.google.com": {"min_interval": 1.00, "max_concurrent": 1, "max_attempts": 1, "rate_limit_cooldown": 900.0},
    "data.bls.gov": {"min_interval": 0.35, "max_concurrent": 2},
    "www.census.gov": {"min_interval": 0.35, "max_concurrent": 2},
    "www2.census.gov": {"min_interval": 0.35, "max_concurrent": 2},
    "www.googleapis.com": {"min_interval": 0.25, "max_concurrent": 2},
    "financialmodelingprep.com": {"min_interval": 0.35, "max_concurrent": 1},
    "plus.kipris.or.kr": {"min_interval": 0.50, "max_concurrent": 1},
}
_DEFAULT_POLICY = {"min_interval": 0.25, "max_concurrent": 2}


def provider_name(url: str) -> str:
    host = (urlsplit(url).hostname or "unknown").lower()
    aliases = {
        "api.gdeltproject.org": "gdelt",
        "data.sec.gov": "sec",
        "www.sec.gov": "sec",
        "naverapihub.apigw.ntruss.com": "naver_api_hub",
        "api.github.com": "github",
        "api.openalex.org": "openalex",
        "api.usaspending.gov": "usaspending",
        "api.stlouisfed.org": "fred",
        "apps.bea.gov": "bea",
        "opendart.fss.or.kr": "opendart",
        "export.arxiv.org": "arxiv",
        "search.patentsview.org": "patentsview",
        "patents.google.com": "google_patents",
        "data.bls.gov": "bls",
        "www.census.gov": "census",
        "www2.census.gov": "census",
        "www.googleapis.com": "googleapis",
        "financialmodelingprep.com": "fmp",
        "plus.kipris.or.kr": "kipris",
    }
    return aliases.get(host, host)


def policy_for_url(url: str, fallback_interval: float = 0.25, fallback_concurrent: int = 2) -> tuple[float, int]:
    host = (urlsplit(url).hostname or "unknown").lower()
    raw = _PROVIDER_POLICIES.get(host)
    if raw is None:
        return max(float(fallback_interval), float(_DEFAULT_POLICY["min_interval"])), max(1, int(fallback_concurrent))
    return max(float(fallback_interval), float(raw["min_interval"])), max(1, min(int(fallback_concurrent), int(raw["max_concurrent"])))


def request_fingerprint(method: str, url: str, payload: Any = None) -> str:
    canonical = json.dumps(
        {"method": method.upper(), "url": url, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(value)
        return max(0.0, seconds)
    except ValueError:
        pass
    try:
        dt = email.utils.parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
    except Exception:
        return None


class ApiHealthRecorder:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._rate_locks: dict[str, threading.Lock] = {}
        self._semaphores: dict[str, threading.BoundedSemaphore] = {}
        self._last_started: dict[str, float] = {}
        self._cooldown_until: dict[str, float] = {}
        self._providers: dict[str, dict[str, Any]] = {}
        self._path = Path(os.environ.get("API_HEALTH_PATH", "outputs/api_health.json"))

    def _entry(self, provider: str) -> dict[str, Any]:
        return self._providers.setdefault(provider, {
            "status": "UNAVAILABLE",
            "network_calls": 0,
            "deduplicated_calls": 0,
            "cache_hits": 0,
            "retries": 0,
            "http_429": 0,
            "http_5xx": 0,
            "timeouts": 0,
            "lkg_uses": 0,
            "fallback_uses": 0,
            "last_success_at": None,
        })

    def record(self, provider: str, event: str, amount: int = 1) -> None:
        with self._lock:
            row = self._entry(provider)
            mapping = {
                "network": "network_calls", "dedupe": "deduplicated_calls", "cache": "cache_hits",
                "retry": "retries", "429": "http_429", "5xx": "http_5xx", "timeout": "timeouts",
                "lkg": "lkg_uses", "fallback": "fallback_uses",
            }
            key = mapping.get(event)
            if key:
                row[key] += amount
            if event == "success":
                row["last_success_at"] = datetime.now(timezone.utc).isoformat()
                row["status"] = "LIVE"
            elif event == "lkg" and row["status"] != "LIVE":
                row["status"] = "LKG"
            elif event == "cache" and row["status"] not in {"LIVE", "LKG"}:
                row["status"] = "CACHE"
            elif event == "fallback" and row["status"] not in {"LIVE", "LKG"}:
                row["status"] = "FALLBACK"
            self.flush()

    def flush(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            existing: dict[str, Any] = {}
            if self._path.is_file():
                try:
                    existing = json.loads(self._path.read_text(encoding="utf-8"))
                except Exception:
                    existing = {}
            merged = dict(existing.get("providers") or {})
            for provider, current in self._providers.items():
                previous = merged.get(provider) or {}
                combined = dict(previous)
                for key in ("network_calls", "deduplicated_calls", "cache_hits", "retries", "http_429", "http_5xx", "timeouts", "lkg_uses", "fallback_uses"):
                    # Existing values belong to earlier processes in the same workflow.
                    base = int(previous.get(key) or 0)
                    prior_local = int(current.get("_persisted_" + key) or 0)
                    delta = int(current.get(key) or 0) - prior_local
                    combined[key] = base + max(0, delta)
                    current["_persisted_" + key] = int(current.get(key) or 0)
                if current.get("last_success_at"):
                    combined["last_success_at"] = current["last_success_at"]
                statuses = [str(previous.get("status") or "UNAVAILABLE"), str(current.get("status") or "UNAVAILABLE")]
                for candidate in ("LIVE", "LKG", "FALLBACK", "CACHE", "UNAVAILABLE"):
                    if candidate in statuses:
                        combined["status"] = candidate
                        break
                merged[provider] = combined
            payload = {
                "schema_version": 1,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "providers": merged,
            }
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(self._path)
        except OSError:
            pass


    def provider_max_attempts(self, url: str, configured_attempts: int) -> int:
        host = (urlsplit(url).hostname or "unknown").lower()
        raw = _PROVIDER_POLICIES.get(host) or {}
        limit = int(raw.get("max_attempts", configured_attempts))
        return max(1, min(int(configured_attempts), limit))

    def rate_limit_cooldown(self, url: str) -> float:
        host = (urlsplit(url).hostname or "unknown").lower()
        raw = _PROVIDER_POLICIES.get(host) or {}
        return max(0.0, float(raw.get("rate_limit_cooldown", 0.0)))

    def cooldown_remaining(self, url: str) -> float:
        provider = provider_name(url)
        with self._lock:
            return max(0.0, self._cooldown_until.get(provider, 0.0) - time.monotonic())

    def activate_cooldown(self, url: str, seconds: float) -> None:
        if seconds <= 0:
            return
        provider = provider_name(url)
        with self._lock:
            self._cooldown_until[provider] = max(
                self._cooldown_until.get(provider, 0.0),
                time.monotonic() + float(seconds),
            )

    def clear_cooldown(self, url: str) -> None:
        provider = provider_name(url)
        with self._lock:
            self._cooldown_until.pop(provider, None)

    @contextmanager
    def slot(self, url: str, fallback_interval: float, fallback_concurrent: int = 2):
        provider = provider_name(url)
        interval, concurrent = policy_for_url(url, fallback_interval, fallback_concurrent)
        with self._lock:
            rate_lock = self._rate_locks.setdefault(provider, threading.Lock())
            semaphore = self._semaphores.setdefault(provider, threading.BoundedSemaphore(concurrent))
        semaphore.acquire()
        try:
            with rate_lock:
                wait = interval - (time.monotonic() - self._last_started.get(provider, 0.0))
                if wait > 0:
                    time.sleep(wait)
                self._last_started[provider] = time.monotonic()
            yield provider
        finally:
            semaphore.release()


_HEALTH = ApiHealthRecorder()


def health() -> ApiHealthRecorder:
    return _HEALTH


def retry_delay(attempt_index: int, base_seconds: float, retry_after: str | None = None) -> float:
    explicit = parse_retry_after(retry_after)
    if explicit is not None:
        return explicit + random.uniform(0.05, 0.35)
    return base_seconds * (2 ** max(0, attempt_index)) + random.uniform(0.15, 0.85)
