from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from ible.integrity import write_json
from ible.v51_walkforward import run_v51
from ible.v60_challenger import run_v60, verify_policy_lock

ROOT = Path(__file__).resolve().parents[1]


class V60ReleaseTests(unittest.TestCase):
    def _build_v51(self, work: Path) -> Path:
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
        out = work / "outputs/v51_test"
        run_v51(work, out, "2026-08-02", v50)
        return out

    def test_policy_lock(self):
        lock = verify_policy_lock(ROOT)
        self.assertEqual(lock["status"], "POLICY_LOCK_VERIFIED")
        self.assertEqual(lock["policy_id"], "WATCH_BRIDGE_STRONG_CONFIRMATION_V1")

    def test_champion_challenger_research_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "repo"
            shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns("outputs", "__pycache__", "*.pyc"))
            (work / "historical_history/v60_audits/v6.0.0-champion-challenger.json").unlink(missing_ok=True)
            v51 = self._build_v51(work)
            summary = run_v60(work, work / "outputs/v60_test", "2026-08-02", v51)
            self.assertEqual(summary["benchmark"]["champion"]["positive_recall"], 0.3333)
            self.assertEqual(summary["benchmark"]["challenger"]["positive_recall"], 0.6667)
            self.assertEqual(summary["benchmark"]["challenger"]["false_alarm_rate"], 0.0)
            self.assertEqual(summary["blind_holdout"]["champion"]["positive_recall"], 0.75)
            self.assertEqual(summary["blind_holdout"]["challenger"]["positive_recall"], 1.0)
            self.assertEqual(summary["blind_holdout"]["challenger"]["false_alarm_rate"], 0.0)
            self.assertTrue(summary["research_gate_passed"])
            self.assertTrue(summary["champion_remains_active"])
            self.assertFalse(summary["challenger_promotion_allowed"])
            self.assertFalse(summary["investment_use_allowed"])
            decisions = json.loads((work / "outputs/v60_test/v60_case_decisions.json").read_text(encoding="utf-8"))
            changed = {row["case_id"] for group in decisions.values() for row in group if row["changed_by_challenger"]}
            self.assertEqual(changed, {"CLOUD_2018", "BLIND_GRID_2020"})

    def test_immutable_comparison_receipt_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "repo"
            shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns("outputs", "__pycache__", "*.pyc"))
            receipt = work / "historical_history/v60_audits/v6.0.0-champion-challenger.json"
            receipt.unlink(missing_ok=True)
            v51 = self._build_v51(work)
            first = run_v60(work, work / "outputs/v60_first", "2026-08-02", v51)
            second = run_v60(work, work / "outputs/v60_second", "2026-08-03", v51)
            self.assertEqual(first["comparison_receipt_action"], "CREATED_IMMUTABLE_COMPARISON_RECEIPT")
            self.assertEqual(second["comparison_receipt_action"], "REUSED_IMMUTABLE_COMPARISON_RECEIPT")

    def test_workflow_only_uses_github_python(self):
        workflow = (ROOT / ".github/workflows/run_v50_final_validator.yml").read_text(encoding="utf-8").lower()
        self.assertIn("ible.v60_cli", workflow)
        self.assertIn("ible.v51_cli", workflow)
        self.assertNotIn("sec.gov", workflow)
        self.assertNotIn("fmp", workflow)
        self.assertNotIn(".bat", workflow)
        self.assertNotIn(".cmd", workflow)


if __name__ == "__main__":
    unittest.main()
