from __future__ import annotations

import json
import unittest
from pathlib import Path

from ible.v32_investment import (
    aggregate_capex,
    aggregate_rd,
    bounded_growth_score,
    normalize_naics_tokens,
    numeric_value,
    parse_aies_capex,
    parse_berd_rd,
    select_naics_rows,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config/release_manifest.json"


class V32ReleaseTests(unittest.TestCase):
    def test_manifest_below_github_web_upload_limit(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        files = list(manifest["files"])
        self.assertEqual(manifest["file_count"], len(files))
        self.assertLess(len(files), 100)
        self.assertEqual(len(files), len(set(files)))
        self.assertEqual([path for path in files if not (ROOT / path).is_file()], [])
        forbidden = [
            path for path in files
            if path.endswith((".bat", ".cmd", ".pyc", ".pyo")) or "__pycache__" in Path(path).parts
        ]
        self.assertEqual(forbidden, [])

    def test_exactly_50_theme_naics_mappings(self):
        payload = json.loads((ROOT / "config/v31_theme_naics.json").read_text(encoding="utf-8"))
        self.assertEqual(len(payload["themes"]), 50)
        self.assertEqual(len({row["theme_id"] for row in payload["themes"]}), 50)

    def test_official_workbooks_parse(self):
        capex = parse_aies_capex((ROOT / "data_seed/official/aies_capex_2023.xlsx").read_bytes())
        rd = parse_berd_rd((ROOT / "data_seed/official/berd_table58_2008_2023.xlsx").read_bytes())
        self.assertGreater(len(capex), 100)
        self.assertGreater(len(rd), 30)
        self.assertTrue(any("3344" in row["codes"] for row in capex))
        self.assertTrue(any("3344" in row["codes"] for row in rd))

    def test_naics_and_suppression_parsing(self):
        self.assertEqual(normalize_naics_tokens("313–16"), ["313", "314", "315", "316"])
        self.assertEqual(normalize_naics_tokens("334510, 334517"), ["334510", "334517"])
        self.assertEqual(normalize_naics_tokens("other 334"), [])
        self.assertEqual(numeric_value("5,234 - 5,245"), 5239.5)
        self.assertIsNone(numeric_value("D"))

    def test_semiconductor_exact_mapping_and_growth(self):
        capex_rows = parse_aies_capex((ROOT / "data_seed/official/aies_capex_2023.xlsx").read_bytes())
        rd_rows = parse_berd_rd((ROOT / "data_seed/official/berd_table58_2008_2023.xlsx").read_bytes())
        capex = aggregate_capex(select_naics_rows(capex_rows, ["3344"]))
        rd = aggregate_rd(select_naics_rows(rd_rows, ["3344"]), 2022, 2023)
        self.assertGreater(capex["total_capex_thousand_usd"], 0)
        self.assertGreater(rd["rd_million_usd_2023"], 0)
        self.assertIsNotNone(rd["rd_growth_ratio"])

    def test_growth_score_is_bounded_and_monotonic(self):
        self.assertGreater(bounded_growth_score(0.20), bounded_growth_score(0.0))
        self.assertGreater(bounded_growth_score(0.0), bounded_growth_score(-0.20))
        self.assertGreaterEqual(bounded_growth_score(-5.0), 0)
        self.assertLessEqual(bounded_growth_score(5.0), 100)

    def test_final_boom_score_remains_prohibited(self):
        config = json.loads((ROOT / "config/v32_investment_sources.json").read_text(encoding="utf-8"))
        self.assertTrue(config["rules"]["never_convert_phase3_signal_to_frozen_boom_score"])
        self.assertFalse(config["rules"]["investment_use_allowed"])


if __name__ == "__main__":
    unittest.main()
