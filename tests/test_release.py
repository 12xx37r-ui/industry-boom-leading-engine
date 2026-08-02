from __future__ import annotations

from pathlib import Path

from ible.github_validation import load_bundle, run_github_validation
from ible.model_lock import load_and_verify_model_lock


ROOT = Path(__file__).resolve().parents[1]


def test_repository_is_below_github_web_upload_limit() -> None:
    ignored = {".git", ".pytest_cache", "__pycache__", "outputs"}
    files = [path for path in ROOT.rglob("*") if path.is_file() and not (set(path.parts) & ignored)]
    assert len(files) < 100, f"repository contains {len(files)} files"


def test_no_local_runner_files() -> None:
    forbidden = {".bat", ".cmd", ".ipynb"}
    found = [path for path in ROOT.rglob("*") if path.is_file() and path.suffix.lower() in forbidden]
    assert found == []


def test_workflow_has_no_sec_or_fmp_collection() -> None:
    text = (ROOT / ".github/workflows/run_v1_github_only.yml").read_text(encoding="utf-8").lower()
    assert "sec.gov" not in text
    assert "data.sec.gov" not in text
    assert "financialmodelingprep" not in text
    assert "fmp" not in text


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
