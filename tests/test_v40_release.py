from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from ible.v40_engine import run_v40
from ible.v40_revenue import (
    aggregate_m3,
    aggregate_qss,
    m3_codes_for_naics,
    parse_m3_series,
    parse_qss_revenue,
    select_naics_rows,
)

ROOT = Path(__file__).resolve().parents[1]


class V40ReleaseTests(unittest.TestCase):
    def test_release_manifest_below_web_upload_limit(self):
        manifest = json.loads((ROOT / "config/release_manifest.json").read_text(encoding="utf-8"))
        files = list(manifest["files"])
        self.assertEqual(manifest["file_count"], len(files))
        self.assertLess(len(files), 100)
        self.assertEqual(len(files), len(set(files)))
        self.assertEqual([item for item in files if not (ROOT / item).is_file()], [])
        self.assertEqual([item for item in files if item.endswith((".bat", ".cmd", ".pyc", ".pyo"))], [])

    def test_official_revenue_workbooks_exist(self):
        for name in (
            "qss_all_current_2026q1.xlsx",
            "m3_shipments_current.xlsx",
            "m3_new_orders_current.xlsx",
        ):
            path = ROOT / "data_seed/official" / name
            self.assertTrue(path.is_file(), name)
            self.assertTrue(path.read_bytes().startswith(b"PK"), name)

    def test_qss_parser_and_naics_selection(self):
        rows = parse_qss_revenue((ROOT / "data_seed/official/qss_all_current_2026q1.xlsx").read_bytes())
        selected = select_naics_rows(rows, ["2211"])
        result = aggregate_qss(selected)
        self.assertGreater(result["current_revenue_million_usd"], 0)
        self.assertIsNotNone(result["revenue_yoy_percent"])

    def test_m3_parser_and_mapping(self):
        shipment_series = parse_m3_series((ROOT / "data_seed/official/m3_shipments_current.xlsx").read_bytes())
        codes = m3_codes_for_naics(["3344"], {"3344": "34S"})
        result = aggregate_m3(shipment_series, codes, "VS")
        self.assertEqual(codes, ["34S"])
        self.assertGreater(result["latest_value_million_usd"], 0)
        self.assertIsNotNone(result["yoy_percent"])

    def test_full_offline_integration(self):
        previous = os.environ.get("IBLE_OFFLINE")
        os.environ["IBLE_OFFLINE"] = "1"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                summary = run_v40(ROOT, Path(tmp), "2026-08-02")
                self.assertEqual(summary["theme_count"], 50)
                self.assertGreaterEqual(summary["direct_commercialization_observed_theme_count"], 45)
                self.assertEqual(summary["model_lock"]["status"], "LOCK_VERIFIED")
                self.assertFalse(summary["investment_use_allowed"])
                observations = json.loads((Path(tmp) / "v40_direct_commercialization_observations.json").read_text(encoding="utf-8"))
                self.assertTrue(all(row["boom_score"] is None for row in observations["themes"]))
                self.assertTrue(any(row["mapping_quality"] == "DIRECT_QSS_NAICS_REVENUE" for row in observations["themes"]))
                self.assertTrue(any(row["mapping_quality"] == "M3_INDUSTRY_SHIPMENTS_PROXY" for row in observations["themes"]))
        finally:
            if previous is None:
                os.environ.pop("IBLE_OFFLINE", None)
            else:
                os.environ["IBLE_OFFLINE"] = previous


if __name__ == "__main__":
    unittest.main()
