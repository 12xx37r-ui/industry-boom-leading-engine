from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from ible.v61_shadow import run_v61, verify_policy_lock

ROOT = Path(__file__).resolve().parents[1]


class V61ReleaseTests(unittest.TestCase):
    def test_release_manifest_and_real_file_count_below_90(self):
        manifest = json.loads((ROOT / "config/release_manifest.json").read_text(encoding="utf-8"))
        files = list(manifest["files"])
        self.assertLess(len(files), 90)
        self.assertEqual(len(files), len(set(files)))
        missing = [relative for relative in files if not (ROOT / relative).is_file()]
        self.assertEqual(missing, [])
        # GitHub 웹 업로드 제한은 이번 릴리즈에 포함되는 파일 수 기준이다.
        # 기존 저장소에 남아 있는 과거 버전 파일까지 세면 덮어쓰기 배포에서
        # 잘못 실패하므로 manifest 등록 파일만 검증한다.
        self.assertEqual(manifest.get("file_count"), len(files))
        forbidden = [relative for relative in files if "__pycache__" in Path(relative).parts or Path(relative).suffix in {".pyc", ".pyo"}]
        self.assertEqual(forbidden, [])

    def test_policy_lock(self):
        result = verify_policy_lock(ROOT)
        self.assertEqual(result["status"], "POLICY_LOCK_VERIFIED")
        self.assertEqual(result["policy_id"], "LIVE_PREVALIDATION_BRIDGE_V1")

    def test_live_snapshot_is_immutable_and_has_no_boom_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "repo"
            shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns("outputs", "__pycache__", "*.pyc"))
            (work / "prospective_history/v61_policy_registry.json").unlink(missing_ok=True)
            shutil.rmtree(work / "prospective_history/v61_policy_snapshots", ignore_errors=True)
            fixture = json.loads((work / "fixture/v61_upstream_fixture.json").read_text(encoding="utf-8"))
            v50 = work / "fixture/runtime_v50"
            v60 = work / "fixture/runtime_v60"
            v50.mkdir(parents=True)
            v60.mkdir(parents=True)
            (v50 / "v50_current_monthly_snapshot.json").write_text(json.dumps(fixture["v50_current_monthly_snapshot"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (v50 / "v50_run_summary.json").write_text(json.dumps(fixture["v50_run_summary"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (v60 / "v60_run_summary.json").write_text(json.dumps(fixture["v60_run_summary"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            first = run_v61(work, work / "outputs/v61_first", "2026-08-02", v50, v60)
            second = run_v61(work, work / "outputs/v61_second", "2026-08-02", v50, v60)
            self.assertEqual(first["snapshot_action"], "CREATED_IMMUTABLE_POLICY_SNAPSHOT")
            self.assertEqual(second["snapshot_action"], "REUSED_IMMUTABLE_POLICY_SNAPSHOT")
            self.assertEqual(first["theme_count"], 50)
            snap = json.loads((work / "outputs/v61_first/v61_current_policy_snapshot.json").read_text(encoding="utf-8"))
            self.assertTrue(all(row["boom_score"] is None for row in snap["decisions"]))
            self.assertGreaterEqual(first["challenger_live_alert_count"], first["champion_live_alert_count"])
            self.assertFalse(first["investment_use_allowed"])

    def test_workflow_is_single_github_path_and_no_legacy_networks(self):
        workflow = (ROOT / ".github/workflows/run_v50_final_validator.yml").read_text(encoding="utf-8").lower()
        self.assertIn("ible.v61_cli", workflow)
        self.assertIn("tests.test_v61_release", workflow)
        self.assertNotIn("sec.gov", workflow)
        self.assertNotIn("fmp", workflow)
        self.assertNotIn(".bat", workflow)
        self.assertNotIn(".cmd", workflow)


if __name__ == "__main__":
    unittest.main()
