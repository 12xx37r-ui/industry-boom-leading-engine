from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from ible.v40_engine import run_v40
from ible.v50_engine import run_v50
from ible.v50_outcomes import add_months, cohort_metrics, percent_change

ROOT = Path(__file__).resolve().parents[1]


class V50ReleaseTests(unittest.TestCase):
    def test_release_manifest_below_github_web_limit(self):
        manifest = json.loads((ROOT / "config/release_manifest.json").read_text(encoding="utf-8"))
        files = list(manifest["files"])
        self.assertEqual(manifest["file_count"], len(files))
        self.assertLess(len(files), 100)
        self.assertEqual(len(files), len(set(files)))
        self.assertEqual([item for item in files if not (ROOT / item).is_file()], [])
        self.assertEqual([item for item in files if item.endswith((".bat", ".cmd", ".pyc", ".pyo"))], [])
        workflows = [item for item in files if item.startswith(".github/workflows/")]
        self.assertEqual(workflows, [".github/workflows/run_v50_final_validator.yml"])

    def test_prohibited_collectors_not_used_by_final_workflow(self):
        workflow = (ROOT / ".github/workflows/run_v50_final_validator.yml").read_text(encoding="utf-8").lower()
        self.assertNotIn("fmp", workflow)
        self.assertNotIn("sec.gov", workflow)
        self.assertNotIn(".bat", workflow)
        self.assertNotIn(".cmd", workflow)
        self.assertIn("ible.v50_cli", workflow)

    def test_v40_theme_specific_coverage_fix(self):
        previous = os.environ.get("IBLE_OFFLINE")
        os.environ["IBLE_OFFLINE"] = "1"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                summary = run_v40(ROOT, Path(tmp), "2026-08-02")
                self.assertEqual(summary["theme_count"], 50)
                self.assertEqual(summary["direct_commercialization_observed_theme_count"], 50)
                payload = json.loads((Path(tmp) / "v40_direct_commercialization_observations.json").read_text(encoding="utf-8"))
                rows = {row["theme_id"]: row for row in payload["themes"]}
                self.assertEqual(rows["SPACE_ECONOMY"]["mapping_quality"], "M3_GUIDED_MISSILE_SPACE_VEHICLE_PROXY")
                self.assertEqual(rows["COMMERCIAL_DRONES"]["mapping_quality"], "M3_BROAD_TRANSPORTATION_EQUIPMENT_PROXY")
        finally:
            if previous is None:
                os.environ.pop("IBLE_OFFLINE", None)
            else:
                os.environ["IBLE_OFFLINE"] = previous

    def test_outcome_math(self):
        self.assertEqual(add_months(__import__("datetime").date(2026, 8, 31), 6).isoformat(), "2027-02-28")
        self.assertEqual(percent_change(120, 100), 20.0)
        rows = [
            {"theme_id": "A", "predicted_score": 90, "realized_outcome_score": 80, "realized_success": True},
            {"theme_id": "B", "predicted_score": 80, "realized_outcome_score": 70, "realized_success": True},
            {"theme_id": "C", "predicted_score": 20, "realized_outcome_score": 30, "realized_success": False},
            {"theme_id": "D", "predicted_score": 10, "realized_outcome_score": 20, "realized_success": False},
        ]
        metrics = cohort_metrics(rows, 2)
        self.assertEqual(metrics["top_success_rate"], 1.0)
        self.assertGreater(metrics["top_bottom_outcome_spread"], 0)
        self.assertGreater(metrics["rank_correlation"], 0)

    def test_full_v50_bootstrap_and_immutable_snapshot(self):
        previous = os.environ.get("IBLE_OFFLINE")
        os.environ["IBLE_OFFLINE"] = "1"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp) / "repo"
                shutil.copytree(ROOT, work)
                shutil.rmtree(work / "prospective_history/v50_snapshots", ignore_errors=True)
                shutil.rmtree(work / "prospective_history/v50_evaluations", ignore_errors=True)
                (work / "prospective_history/v50_snapshot_registry.json").unlink(missing_ok=True)
                (work / "prospective_history/v50_snapshots").mkdir(parents=True)
                (work / "prospective_history/v50_evaluations").mkdir(parents=True)
                v40_out = work / "outputs/v40_test"
                run_v40(work, v40_out, "2026-08-02")
                shutil.copy2(v40_out / "v40_direct_commercialization_observations.json", work / "data_cache/latest/v40_direct_commercialization_observations.json")
                first = run_v50(work, work / "outputs/v50_first", "2026-08-02")
                second = run_v50(work, work / "outputs/v50_second", "2026-08-20")
                self.assertEqual(first["theme_count"], 50)
                self.assertEqual(first["commercialization_coverage_theme_count"], 50)
                self.assertEqual(first["engine_build_progress_percent"], 100)
                self.assertEqual(first["monthly_snapshot_action"], "CREATED_IMMUTABLE_MONTHLY_SNAPSHOT")
                self.assertEqual(second["monthly_snapshot_action"], "REUSED_IMMUTABLE_MONTHLY_SNAPSHOT")
                self.assertEqual(second["monthly_snapshot_count"], 1)
                self.assertFalse(first["investment_use_allowed"])
                ranking = json.loads((work / "outputs/v50_first/v50_candidate_ranking.json").read_text(encoding="utf-8"))
                self.assertTrue(all(row["boom_score"] is None for row in ranking["ranking"]))
        finally:
            if previous is None:
                os.environ.pop("IBLE_OFFLINE", None)
            else:
                os.environ["IBLE_OFFLINE"] = previous

    def test_future_horizon_evaluation_is_created_without_enabling_investment(self):
        previous = os.environ.get("IBLE_OFFLINE")
        os.environ["IBLE_OFFLINE"] = "1"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp) / "repo"
                shutil.copytree(ROOT, work)
                shutil.rmtree(work / "prospective_history/v50_snapshots", ignore_errors=True)
                shutil.rmtree(work / "prospective_history/v50_evaluations", ignore_errors=True)
                (work / "prospective_history/v50_snapshot_registry.json").unlink(missing_ok=True)
                (work / "prospective_history/v50_snapshots").mkdir(parents=True)
                (work / "prospective_history/v50_evaluations").mkdir(parents=True)
                run_v50(work, work / "outputs/v50_baseline", "2026-08-02")
                future = run_v50(work, work / "outputs/v50_future", "2027-02-02")
                self.assertEqual(future["evaluations_created_this_run"], 1)
                self.assertEqual(future["total_matured_evaluation_count"], 1)
                self.assertFalse(future["investment_use_allowed"])
                evaluation = work / "prospective_history/v50_evaluations/2026-08-h06.json"
                self.assertTrue(evaluation.is_file())
                payload = json.loads(evaluation.read_text(encoding="utf-8"))
                self.assertEqual(payload["theme_count"], 50)
                self.assertFalse(payload["investment_use_allowed"])
        finally:
            if previous is None:
                os.environ.pop("IBLE_OFFLINE", None)
            else:
                os.environ["IBLE_OFFLINE"] = previous


if __name__ == "__main__":
    unittest.main()
