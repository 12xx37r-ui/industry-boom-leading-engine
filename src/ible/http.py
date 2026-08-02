from __future__ import annotations

import json
import random
import re
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests


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
    """Thread-safe HTTP client with a global rate limiter and disk cache."""

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
        self.last_request_at = 0.0
        self._local = threading.local()
        self._rate_lock = threading.Lock()
        self._cache_lock = threading.Lock()
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

    def _wait_for_slot(self, min_interval: float | None = None) -> None:
        interval = self.min_interval if min_interval is None else min_interval
        with self._rate_lock:
            wait = interval - (time.monotonic() - self.last_request_at)
            if wait > 0:
                time.sleep(wait)
            self.last_request_at = time.monotonic()

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
        for attempt in range(self.retries + 1):
            self._wait_for_slot(min_interval)
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=headers,
                    timeout=timeout or self.timeout,
                )
                if response.status_code in {429, 500, 502, 503, 504}:
                    err = requests.HTTPError(f"retryable status={response.status_code}")
                    err.response = response
                    raise err
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                last_detail = f"{exc.__class__.__name__}" + (f" status={status}" if status else "")
                if attempt >= self.retries:
                    break
                time.sleep(0.75 * (2**attempt) + random.random() * 0.25)
        raise HttpError(f"{method} failed after {self.retries + 1} attempts: {safe_url}: {last_detail}")

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        cache_key: str | None = None,
        cache_ttl_seconds: int | None = None,
        min_interval: float | None = None,
    ) -> dict[str, Any]:
        cache_path = self._cache_path(cache_key, ".json")
        cached = self._read_cache(cache_path, cache_ttl_seconds, binary=False)
        if isinstance(cached, str):
            return json.loads(cached)
        response = self._request("GET", url, params=params, min_interval=min_interval)
        try:
            payload = response.json()
        except ValueError as exc:
            raise HttpError("Expected JSON response") from exc
        if not isinstance(payload, dict):
            raise HttpError("Expected JSON object")
        self._write_cache(cache_path, json.dumps(payload, ensure_ascii=False))
        return payload

    def post_json(
        self,
        url: str,
        *,
        json_body: dict[str, Any],
        headers: dict[str, str] | None = None,
        cache_key: str | None = None,
        cache_ttl_seconds: int | None = None,
        min_interval: float | None = None,
    ) -> dict[str, Any]:
        cache_path = self._cache_path(cache_key, ".json")
        cached = self._read_cache(cache_path, cache_ttl_seconds, binary=False)
        if isinstance(cached, str):
            return json.loads(cached)
        response = self._request(
            "POST", url, json_body=json_body, headers=headers, min_interval=min_interval
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise HttpError("Expected JSON response") from exc
        if not isinstance(payload, dict):
            raise HttpError("Expected JSON object")
        self._write_cache(cache_path, json.dumps(payload, ensure_ascii=False))
        return payload

    def get_text(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        cache_key: str | None = None,
        cache_ttl_seconds: int | None = None,
        min_interval: float | None = None,
        encoding: str | None = None,
    ) -> str:
        cache_path = self._cache_path(cache_key, ".txt")
        cached = self._read_cache(cache_path, cache_ttl_seconds, binary=False)
        if isinstance(cached, str):
            return cached
        response = self._request("GET", url, params=params, min_interval=min_interval)
        if encoding:
            response.encoding = encoding
        text = response.text
        self._write_cache(cache_path, text)
        return text

    def get_bytes(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        cache_key: str | None = None,
        cache_ttl_seconds: int | None = None,
        min_interval: float | None = None,
        timeout: int | None = None,
    ) -> bytes:
        cache_path = self._cache_path(cache_key, ".bin")
        cached = self._read_cache(cache_path, cache_ttl_seconds, binary=True)
        if isinstance(cached, bytes):
            return cached
        response = self._request(
            "GET", url, params=params, headers=headers, min_interval=min_interval, timeout=timeout
        )
        data = response.content
        self._write_cache(cache_path, data)
        return data
