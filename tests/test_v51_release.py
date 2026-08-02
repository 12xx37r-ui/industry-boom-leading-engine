from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from ible.integrity import file_sha256, write_json
from ible.v51_walkforward import run_v51

ROOT = Path(__file__).resolve().parents[1]


class V51ReleaseTests(unittest.TestCase):
    def test_sealed_seed_manifest(self):
        manifest = json.loads((ROOT / "config/v51_seed_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["sealed_seed_count"], 10)
        for relative, expected in manifest["files"].items():
            path = ROOT / relative
            self.assertTrue(path.is_file(), relative)
            self.assertEqual(file_sha256(path), expected, relative)

    def test_workflow_runs_historical_and_prospective_validators(self):
        text = (ROOT / ".github/workflows/run_v50_final_validator.yml").read_text(encoding="utf-8").lower()
        self.assertIn("ible.v50_cli", text)
        self.assertIn("ible.v51_cli", text)
        self.assertNotIn("sec.gov", text)
        self.assertNotIn("fmp", text)
        self.assertNotIn(".bat", text)
        self.assertNotIn(".cmd", text)

    def test_historical_audit_is_honest_about_recall_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "repo"
            shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns("outputs", "__pycache__", "*.pyc"))
            receipt = work / "historical_history/v51_audits/v5.1.0-sealed-audit.json"
            receipt.unlink(missing_ok=True)
            v50 = work / "outputs/v50_test"
            write_json(v50 / "v50_run_summary.json", {
                "status": "V5_0_PROSPECTIVE_VALIDATION_ACTIVE",
                "theme_count": 50,
                "investment_use_allowed": False,
            })
            write_json(v50 / "v50_dashboard_payload.json", {
                "status": "V5_0_PROSPECTIVE_VALIDATION_ACTIVE",
                "investment_use_allowed": False,
                "ranking": [],
            })
            summary = run_v51(work, work / "outputs/v51_test", "2026-08-02", v50)
            self.assertEqual(summary["benchmark_metrics"]["scenario_count"], 7)
            self.assertEqual(summary["benchmark_metrics"]["positive_recall"], 0.3333)
            self.assertEqual(summary["benchmark_metrics"]["false_alarm_rate"], 0.0)
            self.assertEqual(summary["blind_metrics"]["scenario_count"], 8)
            self.assertEqual(summary["blind_metrics"]["positive_recall"], 0.75)
            self.assertFalse(summary["benchmark_gate_passed"])
            self.assertTrue(summary["blind_gate_passed"])
            self.assertFalse(summary["historical_research_gate_passed"])
            self.assertFalse(summary["external_independence"])
            self.assertFalse(summary["investment_use_allowed"])
            self.assertEqual(summary["status"], "V5_1_HISTORICAL_AUDIT_COMPLETED_RECALL_GAP")

    def test_immutable_audit_receipt_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "repo"
            shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns("outputs", "__pycache__", "*.pyc"))
            receipt = work / "historical_history/v51_audits/v5.1.0-sealed-audit.json"
            receipt.unlink(missing_ok=True)
            v50 = work / "outputs/v50_test"
            write_json(v50 / "v50_run_summary.json", {"status": "ACTIVE", "theme_count": 50, "investment_use_allowed": False})
            write_json(v50 / "v50_dashboard_payload.json", {"status": "ACTIVE", "investment_use_allowed": False, "ranking": []})
            first = run_v51(work, work / "outputs/v51_first", "2026-08-02", v50)
            second = run_v51(work, work / "outputs/v51_second", "2026-08-03", v50)
            self.assertEqual(first["audit_receipt_action"], "CREATED_IMMUTABLE_AUDIT_RECEIPT")
            self.assertEqual(second["audit_receipt_action"], "REUSED_IMMUTABLE_AUDIT_RECEIPT")


if __name__ == "__main__":
    unittest.main()
