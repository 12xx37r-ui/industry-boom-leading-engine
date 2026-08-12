from __future__ import annotations

import hashlib
import json
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from contextlib import nullcontext
from pathlib import Path
from typing import Any


class HttpError(RuntimeError):
    pass


@dataclass(frozen=True)
class HttpSettings:
    timeout_seconds: int = 25
    max_attempts: int = 3
    base_backoff_seconds: float = 2.0
    user_agent: str = "IndustryBoomLeadingEngine/3.0"
    min_interval_seconds: float = 0.20
    cache_ttl_seconds: int = 21600
    stale_if_error_seconds: int = 604800


class JsonHttpClient:
    def __init__(self, settings: HttpSettings, *, cache_dir: str | Path | None = None) -> None:
        self.settings = settings
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._rate_lock = threading.Lock()
        self._last_request_started = 0.0
        self._stats_lock = threading.Lock()
        self._stats = {
            "network_requests": 0,
            "cache_hits": 0,
            "stale_cache_hits": 0,
            "cache_writes": 0,
        }
        self._key_locks_lock = threading.Lock()
        self._key_locks: dict[str, threading.Lock] = {}

    def stats(self) -> dict[str, int]:
        with self._stats_lock:
            return dict(self._stats)

    def _increment(self, key: str) -> None:
        with self._stats_lock:
            self._stats[key] += 1

    def _wait_for_slot(self) -> None:
        with self._rate_lock:
            delay = self.settings.min_interval_seconds - (time.monotonic() - self._last_request_started)
            if delay > 0:
                time.sleep(delay)
            self._last_request_started = time.monotonic()

    def _cache_path(self, url: str, *, method: str, payload: dict[str, Any] | None) -> Path | None:
        if not self.cache_dir:
            return None
        canonical = json.dumps(
            {"method": method, "url": url, "payload": payload},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        digest = hashlib.sha256(canonical).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, path: Path | None, max_age_seconds: int) -> tuple[bytes | None, bool]:
        if not path or not path.is_file():
            return None, False
        age = max(0.0, time.time() - path.stat().st_mtime)
        if age > max_age_seconds:
            return None, False
        try:
            return path.read_bytes(), age > self.settings.cache_ttl_seconds
        except OSError:
            return None, False

    def _write_cache(self, path: Path | None, raw: bytes) -> None:
        if not path:
            return
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(raw)
        temporary.replace(path)
        self._increment("cache_writes")

    def _cache_lock_for(self, path: Path | None):
        if not path:
            return nullcontext()
        key = str(path)
        with self._key_locks_lock:
            lock = self._key_locks.setdefault(key, threading.Lock())
        return lock

    def has_cached_json(self, url: str, *, params: dict[str, Any] | None = None) -> bool:
        if params:
            encoded = urllib.parse.urlencode(sorted(params.items()), doseq=True)
            url = f"{url}{'&' if '?' in url else '?'}{encoded}"
        path = self._cache_path(url, method="GET", payload=None)
        raw, _ = self._read_cache(path, self.settings.cache_ttl_seconds)
        return raw is not None

    def _request_bytes(self, url: str, *, method: str, headers: dict[str, str]) -> bytes:
        last_error = "unknown error"
        for attempt in range(1, self.settings.max_attempts + 1):
            self._wait_for_slot()
            request = urllib.request.Request(url, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=self.settings.timeout_seconds) as response:
                    self._increment("network_requests")
                    return response.read()
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
                if isinstance(exc, urllib.error.HTTPError):
                    body = exc.read(600).decode("utf-8", errors="replace")
                    last_error = f"HTTP {exc.code}: {body}"
                    retryable = exc.code in {408, 425, 429, 500, 502, 503, 504}
                else:
                    last_error = str(exc)
                    retryable = True
                if attempt >= self.settings.max_attempts or not retryable:
                    break
                delay = self.settings.base_backoff_seconds * (2 ** (attempt - 1)) + random.uniform(0.0, 0.7)
                time.sleep(delay)
        raise HttpError(last_error)

    def request_bytes(self, url: str, *, method: str = "GET", accept: str = "*/*") -> bytes:
        return self._request_bytes(url, method=method, headers={
            "Accept": accept,
            "User-Agent": self.settings.user_agent,
        })

    def request_json(
        self,
        url: str,
        *,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        cache_ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        if params:
            encoded = urllib.parse.urlencode(sorted(params.items()), doseq=True)
            url = f"{url}{'&' if '?' in url else '?'}{encoded}"
        cache_path = self._cache_path(url, method=method, payload=payload)
        with self._cache_lock_for(cache_path):
            fresh_ttl = self.settings.cache_ttl_seconds if cache_ttl_seconds is None else cache_ttl_seconds
            stale_ttl = max(fresh_ttl, self.settings.stale_if_error_seconds)
            fresh_raw, _ = self._read_cache(cache_path, fresh_ttl)
            if fresh_raw is not None:
                self._increment("cache_hits")
                return self._decode_json(fresh_raw)
            data = None
            request_headers = {"Accept": "application/json", "User-Agent": self.settings.user_agent}
            if headers:
                request_headers.update({str(key): str(value) for key, value in headers.items()})
            if payload is not None:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                request_headers["Content-Type"] = "application/json"
            try:
                if data is None:
                    raw = self._request_bytes(url, method=method, headers=request_headers)
                else:
                    raw = self._request_payload(url, method=method, headers=request_headers, data=data)
            except HttpError:
                stale_raw, is_stale = self._read_cache(cache_path, stale_ttl)
                if stale_raw is None or not is_stale:
                    raise
                self._increment("stale_cache_hits")
                return self._decode_json(stale_raw)
            self._write_cache(cache_path, raw)
            return self._decode_json(raw)

    def _request_payload(self, url: str, *, method: str, headers: dict[str, str], data: bytes) -> bytes:
        last_error = "unknown error"
        for attempt in range(1, self.settings.max_attempts + 1):
            self._wait_for_slot()
            request = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=self.settings.timeout_seconds) as response:
                    self._increment("network_requests")
                    return response.read()
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
                if isinstance(exc, urllib.error.HTTPError):
                    body = exc.read(600).decode("utf-8", errors="replace")
                    last_error = f"HTTP {exc.code}: {body}"
                    retryable = exc.code in {408, 425, 429, 500, 502, 503, 504}
                else:
                    last_error = str(exc)
                    retryable = True
                if attempt >= self.settings.max_attempts or not retryable:
                    raise HttpError(last_error) from exc
                delay = self.settings.base_backoff_seconds * (2 ** (attempt - 1)) + random.uniform(0.0, 0.7)
                time.sleep(delay)
        raise HttpError(last_error)

    @staticmethod
    def _decode_json(raw: bytes) -> dict[str, Any]:
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HttpError(f"invalid JSON: {exc}") from exc
        if not isinstance(decoded, dict):
            raise HttpError("JSON root is not an object")
        return decoded

    def request_text(
        self,
        url: str,
        *,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        accept: str = "text/csv,text/plain,*/*",
        cache_ttl_seconds: int | None = None,
    ) -> str:
        if params:
            encoded = urllib.parse.urlencode(params, doseq=True)
            url = f"{url}{'&' if '?' in url else '?'}{encoded}"
        cache_path = self._cache_path(url, method=method, payload=None)
        with self._cache_lock_for(cache_path):
            fresh_ttl = self.settings.cache_ttl_seconds if cache_ttl_seconds is None else cache_ttl_seconds
            stale_ttl = max(fresh_ttl, self.settings.stale_if_error_seconds)
            fresh_raw, _ = self._read_cache(cache_path, fresh_ttl)
            if fresh_raw is not None:
                self._increment("cache_hits")
                return fresh_raw.decode("utf-8-sig", errors="replace")
            try:
                raw = self._request_bytes(url, method=method, headers={
                    "Accept": accept,
                    "User-Agent": self.settings.user_agent,
                })
            except HttpError:
                stale_raw, is_stale = self._read_cache(cache_path, stale_ttl)
                if stale_raw is None or not is_stale:
                    raise
                self._increment("stale_cache_hits")
                return stale_raw.decode("utf-8-sig", errors="replace")
            self._write_cache(cache_path, raw)
            return raw.decode("utf-8-sig", errors="replace")
