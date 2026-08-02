from pathlib import Path

from ible.model_lock import load_and_verify_model_lock


def test_v1_model_lock_matches_all_protected_files() -> None:
    root = Path(__file__).resolve().parents[1]
    result = load_and_verify_model_lock(root)
    assert result["status"] == "LOCK_VERIFIED"
    assert result["frozen_model_version"] == "0.9.1"
    assert len(result["checks"]) >= 10
