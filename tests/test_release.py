from __future__ import annotations

import json
from pathlib import Path

from ible.github_validation import load_bundle, run_github_validation
from ible.model_lock import load_and_verify_model_lock


ROOT = Path(__file__).resolve().parents[1]


def test_release_manifest_is_below_github_web_upload_limit() -> None:
    manifest = json.loads((ROOT / "config/release_manifest.json").read_text(encoding="utf-8"))
    files = list(manifest["files"])
    assert len(files) < 100, f"release contains {len(files)} files"
    missing = [relative for relative in files if not (ROOT / relative).is_file()]
    assert missing == [], f"release manifest missing files: {missing}"


def test_no_local_runner_files_in_release() -> None:
    manifest = json.loads((ROOT / "config/release_manifest.json").read_text(encoding="utf-8"))
    forbidden = {".bat", ".cmd", ".ipynb"}
    found = [relative for relative in manifest["files"] if Path(relative).suffix.lower() in forbidden]
    assert found == []


def test_workflow_has_no_sec_or_fmp_collection() -> None:
    text = (ROOT / ".github/workflows/run_v1_github_only.yml").read_text(encoding="utf-8").lower()
    assert "sec.gov" not in text
    assert "data.sec.gov" not in text
    assert "financialmodelingprep" not in text
    assert "test_release.py" in text


def test_model_lock_and_bundle() -> None:
    result = load_and_verify_model_lock(ROOT)
    assert result["status"] == "LOCK_VERIFIED"
    bundle = load_bundle(ROOT / "validation_seed/v1_locked_backtests.json.gz")
    assert len(bundle["cases"]) == 7
    assert bundle["independent_external_holdout"] is False


def test_end_to_end(tmp_path: Path) -> None:
    summary = run_github_validation(ROOT, tmp_path)
    assert summary["execution_mode"] == "github_actions_only"
    assert summary["network_collection_used"] is False
    assert summary["bat_cmd_colab_used"] is False
    assert summary["investment_use_allowed"] is False
    assert summary["independent_external_holdout"]["status"] == "NOT_RUN"
    assert (tmp_path / "v1_github_validation_summary.json").is_file()
