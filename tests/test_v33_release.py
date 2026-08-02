from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ible.v33_engine import _growth_percent_score, _readiness_stage, _weighted_available, run_v33

ROOT = Path(__file__).resolve().parents[1]


class V33ReleaseTests(unittest.TestCase):
    def test_manifest_below_web_upload_limit(self):
        manifest = json.loads((ROOT / "config/release_manifest.json").read_text(encoding="utf-8"))
        files = list(manifest["files"])
        self.assertEqual(manifest["file_count"], len(files))
        self.assertLess(len(files), 100)
        self.assertEqual(len(files), len(set(files)))
        self.assertEqual([p for p in files if not (ROOT / p).is_file()], [])
        self.assertEqual([p for p in files if p.endswith((".bat", ".cmd", ".pyc", ".pyo"))], [])

    def test_required_point_in_time_caches_exist(self):
        for name in (
            "v3_source_observations.json",
            "v31_real_economy_observations.json",
            "v32_corporate_investment_observations.json",
        ):
            self.assertTrue((ROOT / "data_cache/latest" / name).is_file(), name)

    def test_math_helpers_are_bounded(self):
        self.assertAlmostEqual(_weighted_available([(0.5, 40), (0.5, 60)]), 50.0)
        self.assertGreater(_growth_percent_score(20), _growth_percent_score(0))
        self.assertGreater(_growth_percent_score(0), _growth_percent_score(-20))

    def test_stage_requires_multiple_dimensions(self):
        t = {"high_readiness": 65, "watch_readiness": 52}
        self.assertEqual(_readiness_stage(70, 60, 60, t), "HIGH_COMMERCIALIZATION_READINESS")
        self.assertEqual(_readiness_stage(70, 40, 60, t), "EARLY_ACCUMULATION_WATCH")

    def test_full_offline_integration_and_no_boom_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = run_v33(ROOT, Path(tmp), "2026-08-02")
            self.assertEqual(summary["theme_count"], 50)
            self.assertEqual(summary["phase4_observed_theme_count"], 50)
            observations = json.loads((Path(tmp) / "v33_phase4_observations.json").read_text(encoding="utf-8"))
            self.assertTrue(all(row["boom_score"] is None for row in observations["themes"]))
            self.assertFalse(summary["manual_run_required_after_bootstrap"])
            self.assertEqual(summary["model_lock"]["status"], "LOCK_VERIFIED")


if __name__ == "__main__":
    unittest.main()
