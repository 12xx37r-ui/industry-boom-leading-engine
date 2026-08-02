from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ible.gate_receipt import load_and_verify_gate_receipt
from ible.integrity import load_json
from ible.model_lock import load_and_verify_model_lock
from ible.shadow import run_shadow

ROOT = Path(__file__).resolve().parents[1]


class V2ReleaseTests(unittest.TestCase):
    def test_release_manifest_and_forbidden_files(self) -> None:
        manifest = load_json(ROOT / "config/release_manifest.json")
        files = list(manifest["files"])
        self.assertLess(len(files), 100)
        self.assertEqual(len(files), len(set(files)))
        self.assertEqual([name for name in files if not (ROOT / name).is_file()], [])
        self.assertEqual([name for name in files if Path(name).suffix.lower() in {".bat", ".cmd", ".ipynb"}], [])

    def test_workflow_has_no_sec_or_fmp_and_uses_only_v2_test(self) -> None:
        text = (ROOT / ".github/workflows/run_v2_shadow.yml").read_text(encoding="utf-8").lower()
        self.assertNotIn("sec.gov", text)
        self.assertNotIn("financialmodelingprep", text)
        self.assertIn("tests.test_v2_release", text)
        self.assertIn("schedule:", text)
        self.assertIn("contents: write", text)

    def test_previous_gate_and_model_lock(self) -> None:
        self.assertEqual(load_and_verify_model_lock(ROOT)["status"], "LOCK_VERIFIED")
        self.assertEqual(
            load_and_verify_gate_receipt(ROOT / "config/v1_1_gate_receipt.json")["status"],
            "V1_1_GATE_VERIFIED",
        )

    def test_end_to_end_bootstrap_and_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            history = base / "history"
            out1 = base / "out1"
            first = run_shadow(ROOT, out1, run_date="2026-08-02", history_dir_override=history)
            self.assertEqual(first["status"], "V2_SHADOW_BOOTSTRAP_REGISTERED")
            self.assertEqual(first["history_action"], "CREATED")
            self.assertEqual(first["history_count"], 1)
            self.assertFalse(first["current_snapshot_forecast_eligible"])
            second = run_shadow(ROOT, base / "out2", run_date="2026-08-02", history_dir_override=history)
            self.assertEqual(second["history_action"], "DUPLICATE_CONFIRMED")
            self.assertEqual(second["history_count"], 1)
            self.assertTrue((history / "2026/08/2026-08-02.json").is_file())

    def test_stale_input_is_blocked_without_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            summary = run_shadow(ROOT, base / "out", run_date="2026-09-01", history_dir_override=base / "history")
            self.assertEqual(summary["status"], "V2_SHADOW_STALE_INPUT_BLOCKED")
            self.assertEqual(summary["history_action"], "NOT_WRITTEN")
            self.assertEqual(summary["history_count"], 0)


if __name__ == "__main__":
    unittest.main()
