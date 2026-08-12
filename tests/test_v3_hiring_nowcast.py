from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ible.v3_hiring_nowcast import build_hiring_nowcast


class HiringNowcastTests(unittest.TestCase):
    def test_missing_cache_is_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = build_hiring_nowcast(Path(tmp), [{"theme_id": "AI"}], "2026-08-12")
        self.assertEqual(result["status"], "WAITING_FOR_LOCAL_HIRING_CACHE")
        self.assertFalse(result["investment_use_allowed"])

    def test_future_and_duplicate_rows_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "data_cache/inbox"
            path.mkdir(parents=True)
            rows = {"observations": [
                {"theme_id": "AI", "source": "local", "query": "ml engineer", "observed_date": "2026-08-12", "posting_count": 20, "prior_posting_count": 10},
                {"theme_id": "AI", "source": "local", "query": "ml engineer", "observed_date": "2026-08-12", "posting_count": 20, "prior_posting_count": 10},
                {"theme_id": "AI", "source": "local", "query": "ml engineer", "observed_date": "2026-08-13", "posting_count": 99, "prior_posting_count": 1},
            ]}
            (path / "hiring_signal_observations.json").write_text(json.dumps(rows), encoding="utf-8")
            result = build_hiring_nowcast(root, [{"theme_id": "AI"}], "2026-08-12")
        self.assertEqual(result["status"], "HIRING_NOWCAST_OBSERVED")
        self.assertEqual(result["duplicate_observation_count"], 1)
        self.assertEqual(result["future_observation_rejected_count"], 1)
        self.assertEqual(result["themes"][0]["observations"][0]["growth_percent"], 100.0)
