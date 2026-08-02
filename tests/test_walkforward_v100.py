from __future__ import annotations

import json
from pathlib import Path

from ible.config import load_yaml
from ible.walkforward_seed_builder import _load_master
from ible.walkforward_validation import _metrics, _persistence, _verify_model_lock


def test_walkforward_has_two_new_snapshots() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_yaml(root / "config" / "walkforward_holdouts.yml")
    snapshots = config["snapshots"]
    assert [row["id"] for row in snapshots] == ["WF_2019H1", "WF_2019H2"]
    assert all(row["as_of"].startswith("2019-") for row in snapshots)


def test_walkforward_seed_requests_are_point_in_time() -> None:
    root = Path(__file__).resolve().parents[1]
    master = _load_master(root)
    assert master["generator_version"] == "1.0.0"
    assert master["frozen_model_version"] == "0.9.1"
    assert len(master["snapshots"]) == 2
    for snapshot in master["snapshots"]:
        assert len(snapshot["periods"]) >= 6
        assert snapshot["seed_file"].startswith("validation_seed/walkforward/")
        assert len(snapshot["tickers"]) >= 25


def test_model_lock_matches_frozen_v091_files() -> None:
    root = Path(__file__).resolve().parents[1]
    result = _verify_model_lock(root)
    assert result["passed"] is True
    assert result["frozen_model_version"] == "0.9.1"
    assert len(result["files"]) == 3


def test_walkforward_metrics_and_auc() -> None:
    rows = [
        {"label": "positive", "status": "PASSED", "passed": True, "alert_triggered": True, "observed": {"boom_score": 75}},
        {"label": "positive", "status": "FAILED", "passed": False, "alert_triggered": False, "observed": {"boom_score": 50}},
        {"label": "negative", "status": "PASSED", "passed": True, "alert_triggered": False, "observed": {"boom_score": 40}},
    ]
    metrics = _metrics(rows)
    assert metrics["positive_recall"] == 0.5
    assert metrics["false_alarm_rate"] == 0.0
    assert metrics["pairwise_auc"] == 1.0


def test_walkforward_persistence_requires_both_dates() -> None:
    rows = [
        {"target_theme_id": "P", "label": "positive", "status": "PASSED", "as_of": "2019-04-30", "alert_triggered": True},
        {"target_theme_id": "P", "label": "positive", "status": "PASSED", "as_of": "2019-10-31", "alert_triggered": True},
        {"target_theme_id": "N", "label": "negative", "status": "PASSED", "as_of": "2019-04-30", "alert_triggered": False},
        {"target_theme_id": "N", "label": "negative", "status": "PASSED", "as_of": "2019-10-31", "alert_triggered": False},
    ]
    result = _persistence(rows)
    assert result["positive_persistence_rate"] == 1.0
    assert result["negative_rejection_stability"] == 1.0


def test_workflow_uses_zero_network_validation() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / ".github" / "workflows" / "run_independent_walkforward.yml").read_text(encoding="utf-8")
    assert "walkforward_seed_cli --root . --validate-only" in text
    assert "walkforward_cli --root ." in text
    assert "industry-boom-independent-walkforward-v1.0.0" in text
    assert "SEC_USER_AGENT" not in text
    assert "FMP_API_KEY" not in text
