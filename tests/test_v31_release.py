from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path

from ible.v31_qcew import aggregate_naics, parse_qcew_csv, qcew_signal, quarter_candidates


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config/release_manifest.json"


SAMPLE = '''area_fips,own_code,industry_code,agglvl_code,size_code,year,qtr,disclosure_code,qtrly_estabs,month1_emplvl,month2_emplvl,month3_emplvl,total_qtrly_wages\nUS000,5,3344,44,0,2025,1,,100,1000,1100,1200,500000\nUS000,5,5415,44,0,2025,1,,200,2000,2100,2200,800000\nUS000,1,3344,44,0,2025,1,,999,9999,9999,9999,999999\n'''


class V31ReleaseTests(unittest.TestCase):
    def test_exactly_50_unique_theme_mappings(self):
        payload = json.loads((ROOT / "config/v31_theme_naics.json").read_text(encoding="utf-8"))
        rows = payload["themes"]
        ids = [row["theme_id"] for row in rows]
        self.assertEqual(len(rows), 50)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(row["qcew_naics"] for row in rows))

    def test_manifest_is_below_100_and_complete(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        files = list(manifest["files"])
        self.assertEqual(manifest["file_count"], len(files))
        self.assertLess(len(files), 100)
        self.assertEqual(len(files), len(set(files)))
        missing = [path for path in files if not (ROOT / path).is_file()]
        self.assertEqual(missing, [])
        forbidden = [path for path in files if path.endswith((".bat", ".cmd", ".pyc", ".pyo")) or "__pycache__" in Path(path).parts]
        self.assertEqual(forbidden, [])

    def test_qcew_parser_and_aggregate(self):
        rows = parse_qcew_csv(SAMPLE)
        self.assertEqual(len(rows), 3)
        private = [r for r in rows if r["own_code"] == "5"]
        agg = aggregate_naics(private, ["3344"])
        self.assertEqual(agg["matched_naics_count"], 1.0)
        self.assertEqual(agg["employment"], 1100.0)
        self.assertEqual(agg["establishments"], 100.0)

    def test_qcew_signal_is_bounded_and_growth_sensitive(self):
        weights = {"employment_growth": .45, "establishment_growth": .25, "wage_growth": .20, "employment_scale": .10}
        prior = {"matched_naics_count": 1.0, "employment": 1000.0, "establishments": 100.0, "total_quarterly_wages": 500000.0}
        flat = qcew_signal(prior, prior, weights)
        recent = {"matched_naics_count": 1.0, "employment": 1300.0, "establishments": 120.0, "total_quarterly_wages": 700000.0}
        growing = qcew_signal(recent, prior, weights)
        self.assertGreater(growing["source_signal_score"], flat["source_signal_score"])
        self.assertGreaterEqual(growing["source_signal_score"], 0)
        self.assertLessEqual(growing["source_signal_score"], 100)

    def test_quarter_probe_order(self):
        values = quarter_candidates(date(2026, 8, 2), 5)
        self.assertEqual([x.label for x in values], ["2026Q3", "2026Q2", "2026Q1", "2025Q4", "2025Q3"])

    def test_final_boom_score_remains_prohibited(self):
        config = json.loads((ROOT / "config/v31_real_economy_sources.json").read_text(encoding="utf-8"))
        self.assertTrue(config["rules"]["never_convert_phase2_signal_to_frozen_boom_score"])
        self.assertFalse(config["rules"]["investment_use_allowed"])


if __name__ == "__main__":
    unittest.main()
