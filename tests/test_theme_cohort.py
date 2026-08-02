from pathlib import Path

from ible.pipeline import EnginePipeline


def test_theme_rows_can_select_validation_cohort(monkeypatch):
    monkeypatch.delenv("OPENDART_API_KEY", raising=False)
    root = Path(__file__).resolve().parents[1]
    pipeline = EnginePipeline(root)
    selected = pipeline.theme_rows(["AI_COMPUTE_INFRA", "CLOUD_INFRA"])
    assert [row["id"] for row in selected] == ["AI_COMPUTE_INFRA", "CLOUD_INFRA"]
