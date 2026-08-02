from __future__ import annotations

import json
from pathlib import Path

from ible.blind_holdout import load_blind_pack, run_blind_holdout
from ible.github_validation import load_bundle
from ible.model_lock import load_and_verify_model_lock

ROOT = Path(__file__).resolve().parents[1]


def _manifest() -> list[str]:
    payload = json.loads((ROOT / "config/release_manifest.json").read_text(encoding="utf-8"))
    return list(payload["files"])


def test_release_is_below_github_web_upload_limit() -> None:
    files = _manifest()
    assert len(files) < 100
    assert len(files) == len(set(files))
    assert [name for name in files if not (ROOT / name).is_file()] == []


def test_release_has_no_local_runner_or_live_collection() -> None:
    files = _manifest()
    assert [name for name in files if Path(name).suffix.lower() in {".bat", ".cmd", ".ipynb"}] == []
    workflow = (ROOT / ".github/workflows/run_v1_1_blind_holdout.yml").read_text(encoding="utf-8").lower()
    assert "sec.gov" not in workflow
    assert "data.sec.gov" not in workflow
    assert "financialmodelingprep" not in workflow
    assert "tests/test_v1_1_release.py" in workflow


def test_model_and_sealed_packs_are_valid() -> None:
    assert load_and_verify_model_lock(ROOT)["status"] == "LOCK_VERIFIED"
    history = load_bundle(ROOT / "validation_seed/v1_locked_backtests.json.gz")
    blind = load_blind_pack(ROOT / "validation_seed/v1_blind_theme_holdout.json.gz")
    source = {case["scenario_id"]: case for case in history["cases"]}
    assert len(blind["cases"]) == 8
    assert blind["independent_external_holdout"] is False
    for case in blind["cases"]:
        original = source[case["source_scenario_id"]]
        assert case["target_theme_id"] != original["target_theme_id"]


def test_end_to_end_blind_holdout(tmp_path: Path) -> None:
    summary = run_blind_holdout(ROOT, tmp_path)
    assert summary["status"] == "V1_1_BLIND_THEME_HOLDOUT_PASSED"
    assert summary["execution_mode"] == "github_actions_only"
    assert summary["network_collection_used"] is False
    assert summary["bat_cmd_colab_used"] is False
    assert summary["investment_use_allowed"] is False
    assert summary["external_independence"] is False
    assert summary["independent_external_holdout"]["status"] == "NOT_RUN"
    assert summary["metrics"]["positive_recall"] == 0.75
    assert summary["metrics"]["false_alarm_rate"] == 0.0
    assert summary["metrics"]["pairwise_auc"] >= 0.7
    expected = {
        "v1_1_blind_holdout_summary.json",
        "v1_1_blind_holdout_ranking.json",
        "v1_1_blind_holdout_scenarios.json",
        "v1_1_model_lock_verification.json",
        "v1_1_next_gate.json",
    }
    assert expected == {path.name for path in tmp_path.iterdir()}
