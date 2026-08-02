from __future__ import annotations

import json
from pathlib import Path

import pytest

from ible.offline_seed_builder import OfflineSeedError, compute_seed_sha256, validate_seed


def _request() -> dict:
    return {
        "schema_version": 1,
        "generator_version": "0.8.10",
        "cutoff": "2022-04-30",
        "periods": ["2021q1"],
        "source_url_template": "https://example.test/{period}.zip",
        "tickers": [{"ticker": "AAA", "cik": "0000000001"}],
        "research": [{"theme_id": "THEME", "query": "x"}],
        "minimum_financial_coverage": 0.75,
        "minimum_available_companies": 1,
        "minimum_research_themes": 1,
    }


def _seed() -> dict:
    seed = {
        "metadata": {
            "schema_version": 5,
            "version": "0.8.10",
            "normalization_version": "fsds_hybrid_v4_strict_quarter_or_annual_proxy",
            "cutoff": "2022-04-30",
            "requested_tickers": ["AAA"],
        },
        "status": {
            "status": "READY",
            "cutoff": "2022-04-30",
            "periods_required": ["2021q1"],
            "periods_downloaded": ["2021q1"],
            "requested": 1,
            "historically_eligible_count": 1,
            "historically_eligible": ["AAA"],
            "available": 1,
            "available_tickers": ["AAA"],
            "coverage_of_historically_eligible": 1.0,
            "research_required": 1,
            "research_available": 1,
        },
        "series": {
            metric: {"AAA": [["2021-12-31", 1.0]]}
            for metric in ("capex", "rd", "revenue", "gross_profit", "operating_income")
        },
        "tags_used": {},
        "research": {"THEME": {"counts": {"recent": 2, "prior": 1, "older": 0}}},
    }
    seed["metadata"]["content_sha256"] = compute_seed_sha256(seed)
    return seed


def test_validate_seed_accepts_complete_integrity_checked_seed(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "validation_seed").mkdir()
    (tmp_path / "config" / "offline_seed_request.json").write_text(
        json.dumps(_request()), encoding="utf-8"
    )
    (tmp_path / "validation_seed" / "sec_fsds_fy2021.json").write_text(
        json.dumps(_seed()), encoding="utf-8"
    )
    result = validate_seed(tmp_path)
    assert result["status"] == "READY"
    assert result["available"] == 1


def test_validate_seed_rejects_tampering(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "validation_seed").mkdir()
    (tmp_path / "config" / "offline_seed_request.json").write_text(
        json.dumps(_request()), encoding="utf-8"
    )
    seed = _seed()
    seed["series"]["revenue"]["AAA"][0][1] = 999.0
    (tmp_path / "validation_seed" / "sec_fsds_fy2021.json").write_text(
        json.dumps(seed), encoding="utf-8"
    )
    with pytest.raises(OfflineSeedError, match="SHA-256"):
        validate_seed(tmp_path)


def test_validate_seed_fails_fast_when_missing(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "offline_seed_request.json").write_text(
        json.dumps(_request()), encoding="utf-8"
    )
    with pytest.raises(OfflineSeedError, match="1_BUILD_OFFLINE_SEED"):
        validate_seed(tmp_path)
