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
    """Remove credential-like values from arbitrary error text."""
    if not value:
        return value
    redacted = _SECRET_RE.sub(lambda m: f"{m.group(1)}=<redacted>", value)
    try:
        parts = urlsplit(redacted)
        if parts.scheme and parts.netloc and parts.query:
            safe_query = [(k, "<redacted>" if _is_secret_key(k) else v) for k, v in parse_qsl(parts.query, keep_blank_values=True)]
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
    """Thread-safe JSON HTTP client with a global rate limiter and disk cache."""

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
            session.headers.update({"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"})
            self._local.session = session
        return session

    def _wait_for_slot(self) -> None:
        with self._rate_lock:
            wait = self.min_interval - (time.monotonic() - self.last_request_at)
            if wait > 0:
                time.sleep(wait)
            self.last_request_at = time.monotonic()

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        cache_key: str | None = None,
        cache_ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        cache_path = self.cache_dir / f"{cache_key}.json" if self.cache_dir and cache_key else None
        if cache_path and cache_path.exists() and cache_ttl_seconds is not None:
            age = time.time() - cache_path.stat().st_mtime
            if age <= cache_ttl_seconds:
                with self._cache_lock, cache_path.open("r", encoding="utf-8") as handle:
                    return json.load(handle)

        last_detail = "unknown error"
        safe_url = safe_request_url(url, params)
        for attempt in range(self.retries + 1):
            self._wait_for_slot()
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise requests.HTTPError(f"retryable status={response.status_code}")
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise HttpError("Expected JSON object")
                if cache_path:
                    tmp = cache_path.with_suffix(".tmp")
                    with self._cache_lock, tmp.open("w", encoding="utf-8") as handle:
                        json.dump(payload, handle, ensure_ascii=False)
                    tmp.replace(cache_path)
                return payload
            except (requests.RequestException, ValueError, HttpError) as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                last_detail = f"{exc.__class__.__name__}" + (f" status={status}" if status else "")
                if attempt >= self.retries:
                    break
                time.sleep(0.75 * (2**attempt) + random.random() * 0.25)
        raise HttpError(f"GET failed after {self.retries + 1} attempts: {safe_url}: {last_detail}")
