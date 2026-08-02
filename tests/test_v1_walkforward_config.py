from pathlib import Path

from ible.config import load_yaml
from ible.v1_external_holdout import _months_between as external_months_between
from ible.v1_walkforward import _months_between as ai_months_between


def test_ai_panel_is_explicitly_non_independent() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_yaml(root / "config" / "v1_walkforward.yml")
    assert config["validation_role"] == "locked_ai_replay_not_independent"
    assert config["model_version"] == "0.9.1"
    assert config["cohort"]["as_of"] == "2022-10-31"
    assert any(row["target_theme_id"] == "AI_COMPUTE_INFRA" for row in config["scenarios"])


def test_external_holdout_has_balanced_sealed_cohort() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_yaml(root / "config" / "v1_external_holdout.yml")
    exposures = load_yaml(root / "config" / "v1_external_theme_exposures.yml")
    ids = {row["id"] for row in exposures["themes"]}
    labels = [row["label"] for row in config["scenarios"]]
    assert config["validation_role"] == "independent_external_holdout"
    assert config["cohort"]["as_of"] == "2023-06-30"
    assert set(config["cohort"]["theme_ids"]) <= ids
    assert labels.count("positive") >= 4
    assert labels.count("negative") >= 3
    assert all(row.get("us_companies") for row in exposures["themes"])


def test_lead_time_is_forward_only() -> None:
    assert 1.9 <= ai_months_between("2022-10-31", "2023-01-01") <= 2.1
    assert 5.9 <= external_months_between("2023-06-30", "2024-01-01") <= 6.2
