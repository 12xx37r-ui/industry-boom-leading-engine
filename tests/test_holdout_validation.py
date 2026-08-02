from __future__ import annotations

from pathlib import Path

from ible.holdout import aggregate_holdout_results, load_holdout_config, verify_model_lock


def _result(label: str, passed: bool, alert: bool, score: float, rank: int):
    return {
        "status": "PASSED" if passed else "FAILED",
        "label": label,
        "passed": passed,
        "alert_triggered": alert,
        "observed": {"boom_score": score, "rank": rank},
    }


def test_holdout_config_has_balanced_unseen_cohort():
    root = Path(__file__).resolve().parents[1]
    config = load_holdout_config(root)
    labels = [row["label"] for row in config["scenarios"]]
    assert labels.count("positive") == 4
    assert labels.count("negative") == 4
    assert len(config["cohort"]["theme_ids"]) == 8


def test_model_lock_matches_frozen_scoring_files():
    root = Path(__file__).resolve().parents[1]
    result = verify_model_lock(root)
    assert result["matches"] is True
    assert result["status"] == "LOCKED_MODEL_VERIFIED"


def test_stage3_pass_requires_recall_specificity_and_rank_separation():
    rows = [
        _result("positive", True, True, 66, 1),
        _result("positive", True, True, 63, 2),
        _result("positive", True, True, 60, 3),
        _result("positive", False, False, 54, 5),
        _result("negative", True, False, 50, 6),
        _result("negative", True, False, 48, 7),
        _result("negative", True, False, 45, 8),
        _result("negative", True, False, 52, 4),
    ]
    result = aggregate_holdout_results(rows, {"matches": True})
    assert result["stage3_passed"] is True
    assert result["investment_use_allowed"] is False
    assert result["metrics"]["positive_recall"] == 0.75
    assert result["metrics"]["false_alarm_rate"] == 0.0


def test_stage3_fails_when_model_lock_does_not_match():
    rows = [
        _result("positive", True, True, 66, 1),
        _result("positive", True, True, 64, 2),
        _result("positive", True, True, 62, 3),
        _result("positive", True, True, 60, 4),
        _result("negative", True, False, 50, 5),
        _result("negative", True, False, 49, 6),
        _result("negative", True, False, 48, 7),
        _result("negative", True, False, 47, 8),
    ]
    result = aggregate_holdout_results(rows, {"matches": False})
    assert result["stage3_passed"] is False
