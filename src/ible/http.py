from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from ible.api_stability import health, provider_name, request_fingerprint, retry_delay


class HttpError(RuntimeError):
    pass


_SECRET_KEYS = {
    "api_key", "apikey", "user_id", "userid", "crtfc_key", "access_token",
    "token", "secret", "client_secret", "authorization", "auth",
}
_SECRET_RE = re.compile(
    r"(?i)(api_key|apikey|user_id|userid|crtfc_key|access_token|token|secret|client_secret|authorization|auth)"
    r"([=:][^&\s\"']+)"
)


def _is_secret_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    return normalized in _SECRET_KEYS or normalized.endswith("_key") or normalized.endswith("_token")


def redact_text(value: str) -> str:
    if not value:
        return value
    redacted = _SECRET_RE.sub(lambda m: f"{m.group(1)}=<redacted>", value)
    try:
        parts = urlsplit(redacted)
        if parts.scheme and parts.netloc and parts.query:
            safe_query = [
                (k, "<redacted>" if _is_secret_key(k) else v)
                for k, v in parse_qsl(parts.query, keep_blank_values=True)
            ]
            redacted = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(safe_query), parts.fragment))
    except Exception:
        pass
    return redacted


def safe_request_url(url: str, params: dict[str, Any] | None = None) -> str:
    if not params:
        return redact_text(url)
    safe_params = {k: ("<redacted>" if _is_secret_key(k) else v) for k, v in params.items()}
    separator = "&" if "?" in url else "?"
    return redact_text(url + separator + urlencode(safe_params, doseq=True))


class JsonHttpClient:
    """Thread-safe HTTP client with dedupe, provider pacing, bounded retry and disk cache."""

    def __init__(
        self,
        *,
        user_agent: str,
        timeout: int = 12,
        min_interval: float = 0.22,
        retries: int = 1,
        cache_dir: str | Path | None = None,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.min_interval = min_interval
        self.retries = retries
        self._local = threading.local()
        self._cache_lock = threading.Lock()
        self._key_lock_guard = threading.Lock()
        self._key_locks: dict[str, threading.Lock] = {}
        self._memory_lock = threading.Lock()
        self._memory_cache: dict[str, bytes] = {}
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update({
                "User-Agent": self.user_agent,
                "Accept-Encoding": "gzip, deflate",
                "Accept": "application/json,text/plain,text/csv,application/xml,text/xml,*/*",
            })
            self._local.session = session
        return session

    def _key_lock(self, key: str) -> threading.Lock:
        with self._key_lock_guard:
            return self._key_locks.setdefault(key, threading.Lock())

    def _cache_path(self, cache_key: str | None, suffix: str) -> Path | None:
        if not self.cache_dir or not cache_key:
            return None
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", cache_key)[:220]
        return self.cache_dir / f"{safe}{suffix}"

    def _read_cache(self, path: Path | None, ttl: int | None, binary: bool) -> bytes | str | None:
        if not path or not path.exists() or ttl is None:
            return None
        if time.time() - path.stat().st_mtime > ttl:
            return None
        with self._cache_lock:
            return path.read_bytes() if binary else path.read_text(encoding="utf-8")

    def _write_cache(self, path: Path | None, value: bytes | str) -> None:
        if not path:
            return
        tmp = path.with_suffix(path.suffix + ".tmp")
        with self._cache_lock:
            if isinstance(value, bytes):
                tmp.write_bytes(value)
            else:
                tmp.write_text(value, encoding="utf-8")
            tmp.replace(path)

    def _memory_get(self, key: str, url: str) -> bytes | None:
        with self._memory_lock:
            value = self._memory_cache.get(key)
        if value is not None:
            health().record(provider_name(url), "dedupe")
        return value

    def _memory_put(self, key: str, value: bytes) -> None:
        with self._memory_lock:
            self._memory_cache[key] = value

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        min_interval: float | None = None,
        timeout: int | None = None,
    ) -> requests.Response:
        safe_url = safe_request_url(url, params)
        last_detail = "unknown error"
        effective_interval = self.min_interval if min_interval is None else min_interval
        for attempt in range(self.retries + 1):
            if attempt:
                health().record(provider_name(url), "retry")
            retry_after = None
            with health().slot(url, effective_interval, 2) as provider:
                health().record(provider, "network")
                try:
                    response = self.session.request(
                        method, url, params=params, json=json_body, headers=headers,
                        timeout=timeout or self.timeout,
                    )
                    if response.status_code == 429:
                        health().record(provider, "429")
                        retry_after = response.headers.get("Retry-After")
                        raise requests.HTTPError("retryable status=429", response=response)
                    if 500 <= response.status_code <= 599:
                        health().record(provider, "5xx")
                        raise requests.HTTPError(f"retryable status={response.status_code}", response=response)
                    response.raise_for_status()
                    health().record(provider, "success")
                    return response
                except requests.Timeout as exc:
                    health().record(provider, "timeout")
                    last_detail = exc.__class__.__name__
                except requests.RequestException as exc:
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    last_detail = f"{exc.__class__.__name__}" + (f" status={status}" if status else "")
                    if status not in {408, 425, 429, 500, 502, 503, 504, None}:
                        break
            if attempt >= self.retries:
                break
            time.sleep(retry_delay(attempt, 0.75, retry_after))
        raise HttpError(f"{method} failed after {self.retries + 1} attempts: {safe_url}: {last_detail}")

    def _canonical_url(self, url: str, params: dict[str, Any] | None) -> str:
        if not params:
            return url
        return url + ("&" if "?" in url else "?") + urlencode(sorted(params.items()), doseq=True)

    def get_json(self, url: str, *, params: dict[str, Any] | None = None, cache_key: str | None = None,
                 cache_ttl_seconds: int | None = None, min_interval: float | None = None) -> dict[str, Any]:
        canonical = self._canonical_url(url, params)
        key = request_fingerprint("GET", canonical, None)
        memory = self._memory_get(key, url)
        if memory is not None:
            return json.loads(memory.decode("utf-8"))
        with self._key_lock(key):
            memory = self._memory_get(key, url)
            if memory is not None:
                return json.loads(memory.decode("utf-8"))
            cache_path = self._cache_path(cache_key, ".json")
            cached = self._read_cache(cache_path, cache_ttl_seconds, binary=False)
            if isinstance(cached, str):
                health().record(provider_name(url), "cache")
                self._memory_put(key, cached.encode("utf-8"))
                return json.loads(cached)
            response = self._request("GET", url, params=params, min_interval=min_interval)
            try:
                payload = response.json()
            except ValueError as exc:
                raise HttpError("Expected JSON response") from exc
            if not isinstance(payload, dict):
                raise HttpError("Expected JSON object")
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._write_cache(cache_path, raw.decode("utf-8"))
            self._memory_put(key, raw)
            return payload

    def post_json(self, url: str, *, json_body: dict[str, Any], headers: dict[str, str] | None = None,
                  cache_key: str | None = None, cache_ttl_seconds: int | None = None,
                  min_interval: float | None = None) -> dict[str, Any]:
        key = request_fingerprint("POST", url, json_body)
        memory = self._memory_get(key, url)
        if memory is not None:
            return json.loads(memory.decode("utf-8"))
        with self._key_lock(key):
            memory = self._memory_get(key, url)
            if memory is not None:
                return json.loads(memory.decode("utf-8"))
            cache_path = self._cache_path(cache_key, ".json")
            cached = self._read_cache(cache_path, cache_ttl_seconds, binary=False)
            if isinstance(cached, str):
                health().record(provider_name(url), "cache")
                self._memory_put(key, cached.encode("utf-8"))
                return json.loads(cached)
            response = self._request("POST", url, json_body=json_body, headers=headers, min_interval=min_interval)
            try:
                payload = response.json()
            except ValueError as exc:
                raise HttpError("Expected JSON response") from exc
            if not isinstance(payload, dict):
                raise HttpError("Expected JSON object")
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._write_cache(cache_path, raw.decode("utf-8"))
            self._memory_put(key, raw)
            return payload

    def get_text(self, url: str, *, params: dict[str, Any] | None = None, cache_key: str | None = None,
                 cache_ttl_seconds: int | None = None, min_interval: float | None = None,
                 encoding: str | None = None) -> str:
        canonical = self._canonical_url(url, params)
        key = request_fingerprint("GET", canonical, None)
        memory = self._memory_get(key, url)
        if memory is not None:
            return memory.decode(encoding or "utf-8", errors="replace")
        with self._key_lock(key):
            memory = self._memory_get(key, url)
            if memory is not None:
                return memory.decode(encoding or "utf-8", errors="replace")
            cache_path = self._cache_path(cache_key, ".txt")
            cached = self._read_cache(cache_path, cache_ttl_seconds, binary=False)
            if isinstance(cached, str):
                health().record(provider_name(url), "cache")
                self._memory_put(key, cached.encode("utf-8"))
                return cached
            response = self._request("GET", url, params=params, min_interval=min_interval)
            if encoding:
                response.encoding = encoding
            text = response.text
            self._write_cache(cache_path, text)
            self._memory_put(key, text.encode(response.encoding or "utf-8", errors="replace"))
            return text

    def get_bytes(self, url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None,
                  cache_key: str | None = None, cache_ttl_seconds: int | None = None,
                  min_interval: float | None = None, timeout: int | None = None) -> bytes:
        canonical = self._canonical_url(url, params)
        key = request_fingerprint("GET", canonical, None)
        memory = self._memory_get(key, url)
        if memory is not None:
            return memory
        with self._key_lock(key):
            memory = self._memory_get(key, url)
            if memory is not None:
                return memory
            cache_path = self._cache_path(cache_key, ".bin")
            cached = self._read_cache(cache_path, cache_ttl_seconds, binary=True)
            if isinstance(cached, bytes):
                health().record(provider_name(url), "cache")
                self._memory_put(key, cached)
                return cached
            response = self._request("GET", url, params=params, headers=headers, min_interval=min_interval, timeout=timeout)
            data = response.content
            self._write_cache(cache_path, data)
            self._memory_put(key, data)
            return data
