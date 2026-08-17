from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ible.api_stability import health, provider_name, request_fingerprint, retry_delay


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
    max_concurrent_requests: int = 2


class JsonHttpClient:
    """JSON/text client with run-local request dedupe, pacing, bounded retry and LKG cache."""

    def __init__(self, settings: HttpSettings, *, cache_dir: str | Path | None = None) -> None:
        self.settings = settings
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._stats_lock = threading.Lock()
        self._stats = {
            "network_requests": 0,
            "cache_hits": 0,
            "stale_cache_hits": 0,
            "cache_writes": 0,
            "deduplicated_requests": 0,
            "retries": 0,
            "http_429": 0,
            "http_5xx": 0,
            "timeouts": 0,
        }
        self._key_locks_lock = threading.Lock()
        self._key_locks: dict[str, threading.Lock] = {}
        self._memory_lock = threading.Lock()
        self._memory_cache: dict[str, bytes] = {}

    def stats(self) -> dict[str, int]:
        with self._stats_lock:
            return dict(self._stats)

    def _increment(self, key: str) -> None:
        with self._stats_lock:
            self._stats[key] += 1

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

    @staticmethod
    def _cache_age(path: Path | None) -> float | None:
        if not path or not path.is_file():
            return None
        try:
            return max(0.0, time.time() - path.stat().st_mtime)
        except OSError:
            return None

    def _read_cache(self, path: Path | None, max_age_seconds: int) -> bytes | None:
        age = self._cache_age(path)
        if age is None or age > max_age_seconds:
            return None
        try:
            return path.read_bytes() if path else None
        except OSError:
            return None

    def _write_cache(self, path: Path | None, raw: bytes) -> None:
        if not path:
            return
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(raw)
        temporary.replace(path)
        self._increment("cache_writes")

    def _cache_lock_for(self, key: str):
        if not key:
            return nullcontext()
        with self._key_locks_lock:
            lock = self._key_locks.setdefault(key, threading.Lock())
        return lock

    def _memory_get(self, key: str, url: str) -> bytes | None:
        with self._memory_lock:
            raw = self._memory_cache.get(key)
        if raw is not None:
            self._increment("deduplicated_requests")
            self._increment("cache_hits")
            health().record(provider_name(url), "dedupe")
        return raw

    def _memory_put(self, key: str, raw: bytes) -> None:
        with self._memory_lock:
            self._memory_cache[key] = raw

    def has_cached_json(self, url: str, *, params: dict[str, Any] | None = None) -> bool:
        if params:
            encoded = urllib.parse.urlencode(sorted(params.items()), doseq=True)
            url = f"{url}{'&' if '?' in url else '?'}{encoded}"
        path = self._cache_path(url, method="GET", payload=None)
        return self._read_cache(path, self.settings.cache_ttl_seconds) is not None

    def _perform(self, url: str, *, method: str, headers: dict[str, str], data: bytes | None = None) -> bytes:
        last_error = "unknown error"
        recorder = health()
        remaining = recorder.cooldown_remaining(url)
        if remaining > 0:
            raise HttpError(f"provider cooldown active after rate limit ({remaining:.1f}s remaining)")
        max_attempts = recorder.provider_max_attempts(url, self.settings.max_attempts)
        for attempt in range(max_attempts):
            if attempt:
                self._increment("retries")
                health().record(provider_name(url), "retry")
            with health().slot(url, self.settings.min_interval_seconds, self.settings.max_concurrent_requests) as provider:
                request = urllib.request.Request(url, data=data, headers=headers, method=method)
                self._increment("network_requests")
                health().record(provider, "network")
                try:
                    with urllib.request.urlopen(request, timeout=self.settings.timeout_seconds) as response:
                        raw = response.read()
                    health().record(provider, "success")
                    recorder.clear_cooldown(url)
                    return raw
                except urllib.error.HTTPError as exc:
                    body = exc.read(600).decode("utf-8", errors="replace")
                    last_error = f"HTTP {exc.code}: {body}"
                    if exc.code == 429:
                        self._increment("http_429")
                        health().record(provider, "429")
                    elif 500 <= exc.code <= 599:
                        self._increment("http_5xx")
                        health().record(provider, "5xx")
                    retryable = exc.code in {408, 425, 429, 500, 502, 503, 504}
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                except (urllib.error.URLError, TimeoutError) as exc:
                    last_error = str(exc)
                    retryable = True
                    retry_after = None
                    reason = getattr(exc, "reason", None)
                    if isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError) or "timed out" in last_error.lower():
                        self._increment("timeouts")
                        health().record(provider, "timeout")
            if attempt + 1 >= max_attempts or not retryable:
                if retryable and "HTTP 429" in last_error:
                    explicit = retry_delay(attempt, self.settings.base_backoff_seconds, retry_after)
                    recorder.activate_cooldown(url, max(explicit, recorder.rate_limit_cooldown(url)))
                break
            delay = retry_delay(attempt, self.settings.base_backoff_seconds, retry_after)
            if "HTTP 429" in last_error:
                recorder.activate_cooldown(url, delay)
            time.sleep(delay)
        raise HttpError(last_error)

    def _request_bytes(self, url: str, *, method: str, headers: dict[str, str]) -> bytes:
        return self._perform(url, method=method, headers=headers, data=None)

    def request_bytes(self, url: str, *, method: str = "GET", accept: str = "*/*") -> bytes:
        key = request_fingerprint(method, url, None)
        cached = self._memory_get(key, url)
        if cached is not None:
            return cached
        with self._cache_lock_for(key):
            cached = self._memory_get(key, url)
            if cached is not None:
                return cached
            raw = self._request_bytes(url, method=method, headers={"Accept": accept, "User-Agent": self.settings.user_agent})
            self._memory_put(key, raw)
            return raw

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
        key = request_fingerprint(method, url, payload)
        fresh_ttl = self.settings.cache_ttl_seconds if cache_ttl_seconds is None else cache_ttl_seconds
        memory_raw = self._memory_get(key, url)
        if memory_raw is not None:
            return self._decode_json(memory_raw)
        cache_path = self._cache_path(url, method=method, payload=payload)
        with self._cache_lock_for(key):
            memory_raw = self._memory_get(key, url)
            if memory_raw is not None:
                return self._decode_json(memory_raw)
            stale_ttl = max(fresh_ttl, self.settings.stale_if_error_seconds)
            fresh_raw = self._read_cache(cache_path, fresh_ttl)
            if fresh_raw is not None:
                self._increment("cache_hits")
                health().record(provider_name(url), "cache")
                self._memory_put(key, fresh_raw)
                return self._decode_json(fresh_raw)
            request_headers = {"Accept": "application/json", "User-Agent": self.settings.user_agent}
            if headers:
                request_headers.update({str(k): str(v) for k, v in headers.items()})
            data = None
            if payload is not None:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                request_headers["Content-Type"] = "application/json"
            try:
                raw = self._request_bytes(url, method=method, headers=request_headers) if data is None else self._request_payload(url, method=method, headers=request_headers, data=data)
            except HttpError:
                stale_raw = self._read_cache(cache_path, stale_ttl)
                if stale_raw is None or (self._cache_age(cache_path) or 0.0) <= fresh_ttl:
                    raise
                self._increment("stale_cache_hits")
                health().record(provider_name(url), "lkg")
                self._memory_put(key, stale_raw)
                return self._decode_json(stale_raw)
            self._write_cache(cache_path, raw)
            self._memory_put(key, raw)
            return self._decode_json(raw)

    def _request_payload(self, url: str, *, method: str, headers: dict[str, str], data: bytes) -> bytes:
        return self._perform(url, method=method, headers=headers, data=data)

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
            encoded = urllib.parse.urlencode(sorted(params.items()), doseq=True)
            url = f"{url}{'&' if '?' in url else '?'}{encoded}"
        key = request_fingerprint(method, url, None)
        fresh_ttl = self.settings.cache_ttl_seconds if cache_ttl_seconds is None else cache_ttl_seconds
        memory_raw = self._memory_get(key, url)
        if memory_raw is not None:
            return memory_raw.decode("utf-8-sig", errors="replace")
        cache_path = self._cache_path(url, method=method, payload=None)
        with self._cache_lock_for(key):
            memory_raw = self._memory_get(key, url)
            if memory_raw is not None:
                return memory_raw.decode("utf-8-sig", errors="replace")
            stale_ttl = max(fresh_ttl, self.settings.stale_if_error_seconds)
            fresh_raw = self._read_cache(cache_path, fresh_ttl)
            if fresh_raw is not None:
                self._increment("cache_hits")
                health().record(provider_name(url), "cache")
                self._memory_put(key, fresh_raw)
                return fresh_raw.decode("utf-8-sig", errors="replace")
            try:
                raw = self._request_bytes(url, method=method, headers={"Accept": accept, "User-Agent": self.settings.user_agent})
            except HttpError:
                stale_raw = self._read_cache(cache_path, stale_ttl)
                if stale_raw is None or (self._cache_age(cache_path) or 0.0) <= fresh_ttl:
                    raise
                self._increment("stale_cache_hits")
                health().record(provider_name(url), "lkg")
                self._memory_put(key, stale_raw)
                return stale_raw.decode("utf-8-sig", errors="replace")
            self._write_cache(cache_path, raw)
            self._memory_put(key, raw)
            return raw.decode("utf-8-sig", errors="replace")
