from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from ible.v3_collectors import OpenAlexCollector, Period, UsaSpendingCollector, comparison_periods
from ible.v3_data_engine import _cached_source, source_signal
from ible.v3_http import HttpError, HttpSettings, JsonHttpClient
from ible.v3_dynamic_terms import discover_candidates


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "config/release_manifest.json"


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request_json(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class CountingJsonClient(JsonHttpClient):
    def __init__(self, settings, cache_dir):
        super().__init__(settings, cache_dir=cache_dir)
        self.network_calls = 0
        self.fail = False

    def _request_bytes(self, url, *, method, headers):
        self.network_calls += 1
        if self.fail:
            raise HttpError("simulated outage")
        return b'{"meta":{"count":7}}'


def release_files() -> list[str]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return list(manifest["files"])


class V3ReleaseTests(unittest.TestCase):
    def test_query_universe_has_exactly_50_unique_themes(self):
        payload = json.loads((ROOT / "config/v3_theme_queries.json").read_text(encoding="utf-8"))
        ids = [x["theme_id"] for x in payload["themes"]]
        self.assertEqual(len(ids), 50)
        self.assertEqual(len(set(ids)), 50)
        self.assertTrue(all(x["openalex_search"] for x in payload["themes"]))
        self.assertTrue(all(x["usaspending_keywords"] for x in payload["themes"]))

    def test_release_manifest_is_below_github_web_upload_limit(self):
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        files = release_files()
        self.assertEqual(manifest["file_count"], len(files))
        self.assertLess(len(files), manifest["github_web_upload_limit_guard"])
        self.assertEqual(len(files), len(set(files)), "release manifest contains duplicate paths")

        missing = [relative for relative in files if not (ROOT / relative).is_file()]
        self.assertEqual(missing, [], f"release manifest missing files: {missing}")

        forbidden = [
            relative
            for relative in files
            if "__pycache__" in Path(relative).parts or Path(relative).suffix in {".pyc", ".pyo"}
        ]
        self.assertEqual(forbidden, [], f"compiled/cache files must not ship: {forbidden}")

    def test_periods_do_not_overlap(self):
        recent, prior = comparison_periods(date(2026, 8, 1), 365)
        self.assertLess(prior.end, recent.start)
        self.assertEqual((recent.end - recent.start).days + 1, 365)
        self.assertEqual((prior.end - prior.start).days + 1, 365)

    def test_openalex_count_parsing(self):
        client = FakeClient([{"meta": {"count": 123}, "results": []}])
        collector = OpenAlexCollector(client, "https://example.test/works")
        value = collector.count("robotics", Period(date(2025, 1, 1), date(2025, 12, 31)))
        self.assertEqual(value, 123)
        self.assertEqual(client.calls[0][1]["params"]["per-page"], 1)

    def test_usaspending_count_parsing(self):
        client = FakeClient([{"results": {"grants": 2, "contracts": 3, "loans": 1, "direct_payments": 0, "other": 1, "idvs": 4}}])
        collector = UsaSpendingCollector(client, "https://example.test/count")
        value = collector.count(["robotics"], Period(date(2025, 1, 1), date(2025, 12, 31)))
        self.assertEqual(value, 11)
        payload = client.calls[0][1]["payload"]
        self.assertIn("award_type_codes", payload["filters"])

    def test_source_signal_is_bounded_and_scale_sensitive(self):
        low = source_signal(2, 1)
        high = source_signal(2000, 1000)
        self.assertGreater(high["source_signal_score"], low["source_signal_score"])
        self.assertGreaterEqual(low["source_signal_score"], 0)
        self.assertLessEqual(high["source_signal_score"], 100)

    def test_no_final_boom_score_rule_is_locked(self):
        config = json.loads((ROOT / "config/v3_data_sources.json").read_text(encoding="utf-8"))
        self.assertTrue(config["rules"]["never_convert_phase1_signal_to_frozen_boom_score"])
        self.assertFalse(config["rules"]["investment_use_allowed"])

    def test_http_cache_deduplicates_identical_requests(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            client = CountingJsonClient(HttpSettings(max_attempts=1), cache_dir)
            first = client.request_json("https://example.test/works", params={"q": "robotics"})
            second = client.request_json("https://example.test/works", params={"q": "robotics"})
            self.assertEqual(first, second)
            self.assertEqual(client.network_calls, 1)
            self.assertEqual(client.stats()["cache_hits"], 1)

    def test_http_cache_returns_bounded_stale_value_on_outage(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            settings = HttpSettings(max_attempts=1, cache_ttl_seconds=0, stale_if_error_seconds=3600)
            client = CountingJsonClient(settings, cache_dir)
            expected = client.request_json("https://example.test/works", params={"q": "robotics"})
            client.fail = True
            self.assertEqual(client.request_json("https://example.test/works", params={"q": "robotics"}), expected)
            self.assertEqual(client.stats()["stale_cache_hits"], 1)

    def test_historical_run_rejects_future_cache_fallback(self):
        cache = {
            "as_of": "2026-08-11",
            "themes": [{"theme_id": "AI", "sources": {"openalex": {"as_of": "2026-08-11", "source_signal_score": 80}}}],
        }
        self.assertIsNone(_cached_source(cache, "AI", "openalex", "2026-08-01"))
        self.assertIsNotNone(_cached_source(cache, "AI", "openalex", "2026-08-12"))

    def test_dynamic_candidates_require_independent_repeated_evidence(self):
        themes = [{"theme_id": "AI", "theme_name": "artificial intelligence", "openalex_search": "artificial intelligence", "gdelt_query": "AI"}]
        documents = [
            {"document_id": "a", "source": "openalex", "captured_at": "2026-07-01", "text": "artificial intelligence hbm accelerator"},
            {"document_id": "b", "source": "gdelt", "captured_at": "2026-08-01", "text": "artificial intelligence hbm accelerator"},
        ]
        report = discover_candidates(documents, themes, "2026-08-12", min_similarity=0.01)
        self.assertEqual(report["status"], "CANDIDATES_FOUND")
        self.assertFalse(report["auto_add_allowed"])
        self.assertIn("hbm", [row["term"] for row in report["candidates"]])


if __name__ == "__main__":
    unittest.main()
