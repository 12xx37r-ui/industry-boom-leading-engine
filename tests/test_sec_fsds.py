from __future__ import annotations

import json
import zipfile
from pathlib import Path

from ible.collectors.sec_fsds import SecFsdsClient, build_quarterly_series


def _record(date: str, qtrs: int, value: float, tag: str = "Revenues", filed: str = "2022-03-01"):
    return {
        "ticker": "AAA",
        "tag": tag,
        "ddate": date,
        "qtrs": qtrs,
        "value": value,
        "filed": filed,
        "period": date,
        "fy": date[:4],
        "fp": "FY",
        "form": "10-K",
        "adsh": f"x-{date}-{qtrs}",
    }


def test_quarterly_series_prefers_direct_and_derives_cumulative() -> None:
    rows = [
        _record("2021-03-31", 1, 100),
        _record("2021-06-30", 1, 120),
        _record("2021-06-30", 2, 220),
        _record("2021-09-30", 1, 130),
        _record("2021-09-30", 3, 350),
        _record("2021-12-31", 4, 500),
        _record("2022-03-31", 1, 160),
    ]
    tag, series = build_quarterly_series(rows, ["Revenues"])
    assert tag == "Revenues"
    assert series == [
        ("2021-03-31", 100.0),
        ("2021-06-30", 120.0),
        ("2021-09-30", 130.0),
        ("2021-12-31", 150.0),
        ("2022-03-31", 160.0),
    ]


def test_quarterly_series_derives_cashflow_cumulative() -> None:
    rows = [
        _record("2021-03-31", 1, 10, "PaymentsToAcquirePropertyPlantAndEquipment"),
        _record("2021-06-30", 2, 25, "PaymentsToAcquirePropertyPlantAndEquipment"),
        _record("2021-09-30", 3, 45, "PaymentsToAcquirePropertyPlantAndEquipment"),
        _record("2021-12-31", 4, 70, "PaymentsToAcquirePropertyPlantAndEquipment"),
        _record("2022-03-31", 1, 30, "PaymentsToAcquirePropertyPlantAndEquipment"),
    ]
    _, series = build_quarterly_series(rows, ["PaymentsToAcquirePropertyPlantAndEquipment"])
    assert series == [
        ("2021-03-31", 10.0),
        ("2021-06-30", 15.0),
        ("2021-09-30", 20.0),
        ("2021-12-31", 25.0),
        ("2022-03-31", 30.0),
    ]


def _write_dataset(path: Path) -> None:
    sub_header = "adsh\tcik\tname\tform\tperiod\tfy\tfp\tfiled\n"
    sub_rows = [
        "0001-21-q1\t1\tAAA Inc\t10-Q\t20210331\t2021\tQ1\t20210501\n",
        "0001-21-q2\t1\tAAA Inc\t10-Q\t20210630\t2021\tQ2\t20210801\n",
        "0001-21-q3\t1\tAAA Inc\t10-Q\t20210930\t2021\tQ3\t20211101\n",
        "0001-21-fy\t1\tAAA Inc\t10-K\t20211231\t2021\tFY\t20220301\n",
        "0001-22-q1\t1\tAAA Inc\t10-Q\t20220331\t2022\tQ1\t20220420\n",
        "0002-22-q1\t2\tBBB Inc\t10-Q\t20220331\t2022\tQ1\t20220510\n",
    ]
    num_header = "adsh\ttag\tversion\tcoreg\tddate\tqtrs\tuom\tvalue\tfootnote\n"
    num_rows = []
    revenue = [
        ("0001-21-q1", "20210331", 1, 100),
        ("0001-21-q2", "20210630", 1, 120),
        ("0001-21-q3", "20210930", 1, 130),
        ("0001-21-fy", "20211231", 4, 500),
        ("0001-22-q1", "20220331", 1, 160),
    ]
    capex = [
        ("0001-21-q1", "20210331", 1, 10),
        ("0001-21-q2", "20210630", 2, 25),
        ("0001-21-q3", "20210930", 3, 45),
        ("0001-21-fy", "20211231", 4, 70),
        ("0001-22-q1", "20220331", 1, 30),
    ]
    for adsh, ddate, qtrs, value in revenue:
        num_rows.append(f"{adsh}\tRevenues\tus-gaap/2022\t\t{ddate}\t{qtrs}\tUSD\t{value}\t\n")
    for adsh, ddate, qtrs, value in capex:
        num_rows.append(
            f"{adsh}\tPaymentsToAcquirePropertyPlantAndEquipment\tus-gaap/2022\t\t{ddate}\t{qtrs}\tUSD\t{value}\t\n"
        )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("sub.txt", sub_header + "".join(sub_rows))
        zf.writestr("num.txt", num_header + "".join(num_rows))


def test_prepare_seed_filters_filing_date_and_marks_historical_eligibility(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    downloads = cache / "downloads"
    downloads.mkdir(parents=True)
    archive = downloads / "2022q1.zip"
    _write_dataset(archive)
    client = SecFsdsClient(cache, "TestResearch test@example.com", periods=("2022q1",))
    seed_path = tmp_path / "seed.json"
    status = client.prepare_seed(
        {"AAA": "1", "BBB": "2"},
        ["AAA", "BBB"],
        "2022-04-30",
        seed_path,
    )
    assert status["historically_eligible"] == ["AAA"]
    assert status["available_tickers"] == ["AAA"]
    payload = json.loads(seed_path.read_text(encoding="utf-8"))
    assert len(payload["series"]["revenue"]["AAA"]) == 5
    assert payload["series"]["capex"]["AAA"][-1] == ["2022-03-31", 30.0]


def test_load_seed_missing_is_non_throwing(tmp_path: Path) -> None:
    client = SecFsdsClient(tmp_path / "cache", "TestResearch test@example.com")
    series, status, errors = client.load_seed(tmp_path / "missing.json", ["AAA"])
    assert status["status"] == "SEED_MISSING"
    assert series["revenue"]["AAA"] == []
    assert "AAA" in errors


def test_global_holdout_runs_from_ready_offline_seed(tmp_path: Path, monkeypatch) -> None:
    import shutil
    from ible.config import load_yaml
    from ible.global_validation import run_global_holdout
    from ible.collectors.arxiv import ArxivClient

    project = Path(__file__).resolve().parents[1]
    (tmp_path / "config").mkdir()
    for name in ("global_holdouts.yml", "theme_exposures.yml", "sec_cik_map.json"):
        shutil.copy2(project / "config" / name, tmp_path / "config" / name)
    exposure = load_yaml(tmp_path / "config" / "theme_exposures.yml")
    holdout = load_yaml(tmp_path / "config" / "global_holdouts.yml")
    minimum = float(exposure["minimum_exposure"])
    cohort = set(holdout["cohort"]["theme_ids"])
    tickers = sorted({
        company["ticker"]
        for theme in exposure["themes"]
        if theme["id"] in cohort
        for company in theme["us_companies"]
        if float(company["exposure"]) >= minimum
    })
    dates = ["2021-03-31", "2021-06-30", "2021-09-30", "2021-12-31", "2022-03-31"]
    series = {
        metric: {
            ticker: [[date, float((index + 1) * 100)] for index, date in enumerate(dates)]
            for ticker in tickers
        }
        for metric in ("capex", "rd", "revenue", "gross_profit", "operating_income")
    }
    theme_ids = list(holdout["cohort"]["theme_ids"])
    research = {
        theme_id: {"counts": {"recent": 120, "prior": 90, "older": 70}}
        for theme_id in theme_ids
    }
    status = {
        "status": "READY",
        "requested": len(tickers),
        "historically_eligible_count": len(tickers),
        "historically_eligible": tickers,
        "available": len(tickers),
        "available_tickers": tickers,
        "coverage_of_historically_eligible": 1.0,
        "periods_required": ["x"],
        "periods_downloaded": ["x"],
        "research_required": len(theme_ids),
        "research_available": len(theme_ids),
        "research_errors": {},
        "company_status": {ticker: {"usable": True} for ticker in tickers},
    }
    seed = {
        "metadata": {
            "cutoff": "2022-04-30",
            "requested_tickers": tickers,
        },
        "status": status,
        "series": series,
        "tags_used": {},
        "research": research,
    }
    seed_dir = tmp_path / "validation_seed"
    seed_dir.mkdir()
    (seed_dir / "sec_fsds_fy2021.json").write_text(json.dumps(seed), encoding="utf-8")
    monkeypatch.setenv("SEC_USER_AGENT", "TestResearch test@example.com")
    monkeypatch.setattr(
        ArxivClient,
        "momentum",
        lambda self, query, cutoff: {"count_recent": 100, "count_prior": 80, "growth": 0.25},
    )
    summary = run_global_holdout(tmp_path, tmp_path / "outputs")
    assert summary["dataset_gate_passed"] is True
    assert summary["status"] in {"PASSED_V087_GLOBAL_HOLDOUT", "FAILED_V087_GLOBAL_HOLDOUT"}
    assert len(summary["ranking"]) == 7
