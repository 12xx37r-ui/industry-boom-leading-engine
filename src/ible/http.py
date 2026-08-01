from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any

import requests


class HttpError(RuntimeError):
    pass


class JsonHttpClient:
    def __init__(
        self,
        *,
        user_agent: str,
        timeout: int = 30,
        min_interval: float = 0.0,
        retries: int = 4,
        cache_dir: str | Path | None = None,
    ) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
        self.timeout = timeout
        self.min_interval = min_interval
        self.retries = retries
        self.last_request_at = 0.0
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

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
                with cache_path.open("r", encoding="utf-8") as handle:
                    return json.load(handle)

        for attempt in range(self.retries + 1):
            wait = self.min_interval - (time.monotonic() - self.last_request_at)
            if wait > 0:
                time.sleep(wait)
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                self.last_request_at = time.monotonic()
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise requests.HTTPError(f"retryable status={response.status_code}")
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise HttpError(f"Expected JSON object from {url}")
                if cache_path:
                    with cache_path.open("w", encoding="utf-8") as handle:
                        json.dump(payload, handle, ensure_ascii=False)
                return payload
            except (requests.RequestException, ValueError, HttpError) as exc:
                if attempt >= self.retries:
                    raise HttpError(f"GET failed after retries: {url}: {exc}") from exc
                time.sleep((2**attempt) + random.random())
        raise AssertionError("unreachable")
