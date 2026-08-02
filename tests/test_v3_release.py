from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path

from ible.v3_collectors import OpenAlexCollector, Period, UsaSpendingCollector, comparison_periods
from ible.v3_data_engine import source_signal


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "config/release_manifest.json"


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request_json(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


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


if __name__ == "__main__":
    unittest.main()
