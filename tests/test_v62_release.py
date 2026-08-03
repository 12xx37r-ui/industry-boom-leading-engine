from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from ible.v50_engine import run_v50
from ible.v51_walkforward import run_v51
from ible.v60_challenger import run_v60
from ible.v61_shadow import run_v61, verify_policy_lock

ROOT = Path(__file__).resolve().parents[1]


class V7ReleaseTests(unittest.TestCase):
    def test_release_manifest_below_90(self):
        manifest = json.loads(
            (ROOT / "config/release_manifest.json").read_text(encoding="utf-8")
        )
        files = manifest["files"]
        self.assertLess(len(files), 90)
        self.assertEqual(len(files), len(set(files)))
        self.assertEqual([], [f for f in files if not (ROOT / f).is_file()])
        self.assertEqual(manifest["file_count"], len(files))
        self.assertFalse(any(f.startswith("google_apps_script/") for f in files))

    def test_policy_lock(self):
        result = verify_policy_lock(ROOT)
        self.assertEqual(result["status"], "POLICY_LOCK_VERIFIED")
        self.assertEqual(result["policy_id"], "V7_COMPLETE_OPERATIONAL_POLICY_V1")

    def test_all_four_missing_layers_are_filled(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "repo"
            shutil.copytree(
                ROOT,
                work,
                ignore=shutil.ignore_patterns("outputs", "__pycache__", "*.pyc"),
            )
            fixture = json.loads(
                (work / "fixture/v61_upstream_fixture.json").read_text(
                    encoding="utf-8"
                )
            )
            v50 = work / "fixture/runtime_v50"
            v60 = work / "fixture/runtime_v60"
            v50.mkdir(parents=True)
            v60.mkdir(parents=True)
            (v50 / "v50_current_monthly_snapshot.json").write_text(
                json.dumps(
                    fixture["v50_current_monthly_snapshot"],
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            (v50 / "v50_run_summary.json").write_text(
                json.dumps(
                    fixture["v50_run_summary"], ensure_ascii=False, indent=2
                )
                + "\n",
                encoding="utf-8",
            )
            (v60 / "v60_run_summary.json").write_text(
                json.dumps(
                    fixture["v60_run_summary"], ensure_ascii=False, indent=2
                )
                + "\n",
                encoding="utf-8",
            )

            summary = run_v61(
                work,
                work / "outputs/v70",
                "2026-08-04",
                v50,
                v60,
            )
            snapshot = json.loads(
                (work / "outputs/v70/v70_current_operational_snapshot.json").read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual(summary["theme_count"], 50)
            self.assertEqual(summary["public_interest_coverage_count"], 50)
            self.assertEqual(summary["boom_score_count"], 50)
            self.assertEqual(summary["three_month_change_count"], 50)
            self.assertEqual(summary["company_mapping_count"], 50)
            self.assertTrue(
                all(
                    row["public_interest_score"] is not None
                    and row["boom_score"] is not None
                    and row["score_change_3m"] is not None
                    and len(row["companies"]) > 0
                    for row in snapshot["decisions"]
                )
            )

    def test_v60_immutable_receipt_survives_later_run_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "repo"
            shutil.copytree(
                ROOT,
                work,
                ignore=shutil.ignore_patterns("outputs", "__pycache__", "*.pyc"),
            )
            v50 = work / "outputs/v50_final_validator"
            v51 = work / "outputs/v51_historical_audit"
            v60 = work / "outputs/v60_champion_challenger"

            run_v50(work, v50, "2026-08-04")
            run_v51(work, v51, "2026-08-04", v50)
            summary = run_v60(work, v60, "2026-08-04", v51)

            self.assertEqual(
                summary["comparison_receipt_action"],
                "REUSED_IMMUTABLE_COMPARISON_RECEIPT",
            )
            sealed = json.loads(
                (work / "historical_history/v60_audits/v6.0.0-champion-challenger.json").read_text(
                    encoding="utf-8"
                )
            )
            emitted = json.loads(
                (v60 / "v60_champion_challenger_comparison.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(sealed["comparison_sha256"], emitted["comparison_sha256"])
            self.assertEqual("2026-08-02", emitted["evidence_as_of"])

    def test_queries_cover_interest_sources(self):
        queries = json.loads(
            (ROOT / "config/v3_theme_queries.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(queries["themes"]), 50)
        self.assertTrue(
            all(
                row.get("gdelt_query") and row.get("wikipedia_titles")
                for row in queries["themes"]
            )
        )

    def test_workflow_and_engine_contract(self):
        workflow = (
            ROOT / ".github/workflows/run_v50_final_validator.yml"
        ).read_text(encoding="utf-8")
        engine = (ROOT / "src/ible/v61_shadow.py").read_text(encoding="utf-8")

        self.assertIn("Industry Boom V7.0.5 Engine Only", workflow)
        self.assertIn("outputs/v70_final_engine", workflow)
        self.assertNotIn("google_apps_script", workflow)
        self.assertNotIn("sec.gov", workflow.lower())
        self.assertNotIn("fmp", workflow.lower())
        self.assertIn("v70_dashboard_payload.json", engine)
        self.assertIn("public_interest_score", engine)
        self.assertIn("hidden_opportunity_score", engine)
        self.assertIn("score_change_3m", engine)
        self.assertIn("company_mapping_count", engine)


if __name__ == "__main__":
    unittest.main()
