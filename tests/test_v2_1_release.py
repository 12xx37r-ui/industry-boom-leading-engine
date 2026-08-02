from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ible.gate_receipt import load_and_verify_gate_receipt
from ible.integrity import load_json
from ible.model_lock import load_and_verify_model_lock
from ible.shadow import run_shadow
from ible.universe import build_universe_status, load_and_validate_indicator_contract, load_and_validate_universe

ROOT = Path(__file__).resolve().parents[1]


class V21ReleaseTests(unittest.TestCase):
    def test_release_manifest_and_forbidden_files(self) -> None:
        manifest = load_json(ROOT / "config/release_manifest.json")
        files = list(manifest["files"])
        self.assertLess(len(files), 100)
        self.assertEqual(len(files), len(set(files)))
        self.assertEqual([name for name in files if not (ROOT / name).is_file()], [])
        self.assertEqual([name for name in files if Path(name).suffix.lower() in {".bat", ".cmd", ".ipynb"}], [])

    def test_workflow_is_github_only_and_replaces_v2(self) -> None:
        text = (ROOT / ".github/workflows/run_v2_shadow.yml").read_text(encoding="utf-8").lower()
        self.assertNotIn("sec.gov", text)
        self.assertNotIn("financialmodelingprep", text)
        self.assertIn("tests.test_v2_1_release", text)
        self.assertIn("50-theme", text)
        self.assertIn("contents: write", text)

    def test_model_lock_and_previous_gate(self) -> None:
        self.assertEqual(load_and_verify_model_lock(ROOT)["status"], "LOCK_VERIFIED")
        self.assertEqual(load_and_verify_gate_receipt(ROOT / "config/v1_1_gate_receipt.json")["status"], "V1_1_GATE_VERIFIED")

    def test_universe_has_50_and_no_fabricated_scores(self) -> None:
        universe = load_and_validate_universe(ROOT / "config/theme_universe.json", 50)
        contract = load_and_validate_indicator_contract(ROOT / "config/indicator_contract.json")
        snapshot = load_json(ROOT / "shadow_input/current_snapshot.json")
        status, backlog = build_universe_status(universe, snapshot["ranking"])
        self.assertEqual(status["theme_count"], 50)
        self.assertEqual(status["scored_theme_count"], 7)
        self.assertEqual(status["pending_theme_count"], 43)
        self.assertEqual(status["fabricated_score_count"], 0)
        self.assertEqual(len(contract["required_dimensions"]), 8)
        self.assertEqual(len(backlog["themes"]), 43)

    def test_end_to_end_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            summary = run_shadow(ROOT, base / "out", run_date="2026-08-02", history_dir_override=base / "history")
            self.assertEqual(summary["status"], "V2_SHADOW_BOOTSTRAP_REGISTERED")
            self.assertEqual(summary["theme_universe"]["theme_count"], 50)
            self.assertEqual(summary["theme_universe"]["pending_theme_count"], 43)
            for name in (
                "v2_shadow_summary.json",
                "v2_shadow_current.json",
                "v2_shadow_ledger.json",
                "v2_shadow_scorecard_queue.json",
                "v2_model_lock_verification.json",
                "v2_next_gate.json",
                "v2_1_theme_universe_status.json",
                "v2_1_data_backlog.json",
                "v2_1_indicator_contract.json",
            ):
                self.assertTrue((base / "out" / name).is_file(), name)

    def test_included_history_is_integrity_checked(self) -> None:
        history = load_json(ROOT / "shadow_history/2026/08/2026-08-02.json")
        self.assertEqual(history["frozen_model_version"], "0.9.1")
        self.assertEqual(len(history["ranking"]), 7)


if __name__ == "__main__":
    unittest.main()
