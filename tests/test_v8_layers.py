from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from ible.v8_layers import _classification_metrics, run_v8


ROOT = Path(__file__).resolve().parents[1]


class V8LayerTests(unittest.TestCase):
    def test_classification_metrics_reports_confusion_matrix(self):
        rows = [
            {"theme_id": "A", "predicted_score": 80, "realized_outcome_score": 75, "realized_success": True},
            {"theme_id": "B", "predicted_score": 70, "realized_outcome_score": 40, "realized_success": False},
            {"theme_id": "C", "predicted_score": 30, "realized_outcome_score": 70, "realized_success": True},
            {"theme_id": "D", "predicted_score": 20, "realized_outcome_score": 30, "realized_success": False},
        ]
        metrics = _classification_metrics(rows, 60.0)
        self.assertEqual(metrics["true_positive"], 1)
        self.assertEqual(metrics["false_positive"], 1)
        self.assertEqual(metrics["false_negative"], 1)
        self.assertEqual(metrics["true_negative"], 1)
        self.assertEqual(metrics["precision"], 0.5)
        self.assertEqual(metrics["recall"], 0.5)

    def test_v8_keeps_locked_score_and_emits_all_layers(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "repo"
            shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns("outputs", "__pycache__", "*.pyc"))
            v70 = work / "outputs/v70_final_engine"
            v70.mkdir(parents=True)
            decisions = [
                {
                    "theme_id": "DATA_CENTER_INFRASTRUCTURE",
                    "theme_name": "데이터센터 인프라",
                    "sector": "AI·컴퓨팅",
                    "rank": 1,
                    "predicted_score": 70.0,
                    "boom_score": 62.0,
                    "hidden_opportunity_score": 68.0,
                    "public_interest_score": 30.0,
                    "direct_commercialization_score": 70.0,
                    "phase3_investment_score": 65.0,
                    "source_diffusion_percent": 80.0,
                },
                {
                    "theme_id": "SPACE_ECONOMY",
                    "theme_name": "우주산업",
                    "sector": "우주·방산",
                    "rank": 2,
                    "predicted_score": 50.0,
                    "boom_score": 48.0,
                    "hidden_opportunity_score": 55.0,
                    "public_interest_score": 60.0,
                    "direct_commercialization_score": 45.0,
                    "phase3_investment_score": 40.0,
                    "source_diffusion_percent": 40.0,
                },
            ]
            snapshot = {"snapshot_id": "2026-08", "as_of": "2026-08-17", "content_sha256": "fixture", "decisions": decisions}
            (v70 / "v70_current_operational_snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")
            summary = run_v8(work, work / "outputs/v8_layers", "2026-08-17", v70)
            self.assertEqual(summary["theme_count"], 2)
            self.assertFalse(summary["locked_score_mutated"])
            layer = json.loads((work / "outputs/v8_layers/v8_current_layer_snapshot.json").read_text(encoding="utf-8"))
            self.assertEqual(layer["themes"][0]["locked_score_unchanged"], True)
            self.assertIn("hidden_interaction", layer["themes"][0])
            for name in ("v8_validation_scorecard.json", "v8_proxy_quality_report.json", "v8_discovery_challengers.json"):
                self.assertTrue((work / "outputs/v8_layers" / name).is_file())


if __name__ == "__main__":
    unittest.main()
