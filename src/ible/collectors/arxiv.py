from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET
from typing import Any

from ible.http import JsonHttpClient


class ArxivClient:
    """Small, cache-first client for arXiv's public Atom API.

    The public API asks clients to avoid rapid request bursts. The shared HTTP
    client therefore enforces a 3.1 second interval for uncached arXiv calls.
    """

    BASE_URL = "https://export.arxiv.org/api/query"
    NS = {
        "atom": "http://www.w3.org/2005/Atom",
        "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
    }

    def __init__(self, http: JsonHttpClient) -> None:
        self.http = http

    @staticmethod
    def _date_token(value: dt.date, end_of_day: bool = False) -> str:
        return value.strftime("%Y%m%d") + ("2359" if end_of_day else "0000")

    @classmethod
    def parse_total_results(cls, atom_text: str) -> int:
        root = ET.fromstring(atom_text)
        node = root.find("opensearch:totalResults", cls.NS)
        if node is None or node.text is None:
            raise ValueError("arXiv response did not include totalResults")
        return int(node.text.strip())

    def total_results(self, query: str | None, start: dt.date, end: dt.date) -> int:
        date_filter = (
            f"submittedDate:[{self._date_token(start)} TO {self._date_token(end, True)}]"
        )
        search_query = f"({query}) AND {date_filter}" if query else date_filter
        cache_query = (query or "ALL").replace(" ", "_")[:100]
        text = self.http.get_text(
            self.BASE_URL,
            params={
                "search_query": search_query,
                "start": 0,
                "max_results": 1,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            },
            cache_key=f"arxiv_count_{cache_query}_{start.isoformat()}_{end.isoformat()}",
            cache_ttl_seconds=31536000 if end < dt.date.today() - dt.timedelta(days=45) else 86400,
            min_interval=3.1,
        )
        return self.parse_total_results(text)

    def momentum(self, query: str, as_of: dt.date) -> dict[str, Any]:
        windows: list[tuple[str, dt.date, dt.date]] = []
        cursor_end = as_of
        for label in ("recent", "prior", "older"):
            cursor_start = cursor_end - dt.timedelta(days=364)
            windows.append((label, cursor_start, cursor_end))
            cursor_end = cursor_start - dt.timedelta(days=1)

        counts: dict[str, int] = {}
        for label, start, end in windows:
            counts[label] = self.total_results(query, start, end)
        return {
            "query": query,
            "as_of": as_of.isoformat(),
            "windows": {
                label: {"start": start.isoformat(), "end": end.isoformat(), "count": counts[label]}
                for label, start, end in windows
            },
            "counts": counts,
        }
