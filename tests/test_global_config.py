from pathlib import Path
from ible.config import load_yaml


def test_global_exposure_config_has_positive_and_negative_cohorts():
    root = Path(__file__).resolve().parents[1]
    exposure = load_yaml(root/'config'/'theme_exposures.yml')
    holdout = load_yaml(root/'config'/'global_holdouts.yml')
    ids = {t['id'] for t in exposure['themes']}
    assert set(holdout['cohort']['theme_ids']) <= ids
    labels = [s['label'] for s in holdout['scenarios']]
    assert labels.count('positive') >= 4
    assert labels.count('negative') >= 3
