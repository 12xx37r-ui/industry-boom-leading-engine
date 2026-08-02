from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


class HttpError(RuntimeError):
    pass


@dataclass(frozen=True)
class HttpSettings:
    timeout_seconds: int = 25
    max_attempts: int = 3
    base_backoff_seconds: float = 2.0
    user_agent: str = "IndustryBoomLeadingEngine/3.0"


class JsonHttpClient:
    def __init__(self, settings: HttpSettings) -> None:
        self.settings = settings

    def request_json(
        self,
        url: str,
        *,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if params:
            encoded = urllib.parse.urlencode(params, doseq=True)
            url = f"{url}{'&' if '?' in url else '?'}{encoded}"
        data = None
        headers = {
            "Accept": "application/json",
            "User-Agent": self.settings.user_agent,
        }
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        last_error = "unknown error"
        for attempt in range(1, self.settings.max_attempts + 1):
            request = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=self.settings.timeout_seconds) as response:
                    raw = response.read()
                    decoded = json.loads(raw.decode("utf-8"))
                    if not isinstance(decoded, dict):
                        raise HttpError("JSON root is not an object")
                    return decoded
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, HttpError) as exc:
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
