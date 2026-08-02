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
    assert summary["status"] in {"PASSED_V0810_GLOBAL_HOLDOUT", "FAILED_V0810_GLOBAL_HOLDOUT"}
    assert len(summary["ranking"]) == 7


def test_quarterly_series_never_mixes_xbrl_tags() -> None:
    rows = [
        _record("2020-03-31", 1, 100, "Revenues"),
        _record("2020-09-30", 1, 120, "Revenues"),
        _record("2021-03-31", 1, 140, "Revenues"),
        _record("2020-06-30", 1, 9999, "SalesRevenueNet"),
        _record("2020-12-31", 1, 9999, "SalesRevenueNet"),
        _record("2021-06-30", 1, 9999, "SalesRevenueNet"),
    ]
    tag, series = build_quarterly_series(rows, ["Revenues", "SalesRevenueNet"], "revenue")
    assert tag == "Revenues"
    assert len(series) == 3
    assert {value for _, value in series} == {100.0, 120.0, 140.0}


def test_quarterly_series_rejects_negative_nonnegative_flow() -> None:
    rows = [
        _record("2021-03-31", 1, 10, "PaymentsToAcquirePropertyPlantAndEquipment"),
        _record("2021-06-30", 2, 5, "PaymentsToAcquirePropertyPlantAndEquipment"),
        _record("2021-09-30", 3, 40, "PaymentsToAcquirePropertyPlantAndEquipment"),
    ]
    _, series = build_quarterly_series(
        rows, ["PaymentsToAcquirePropertyPlantAndEquipment"], "capex"
    )
    assert ("2021-06-30", -5.0) not in series


def test_prepare_seed_ignores_coreg_numbers(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    downloads = cache / "downloads"
    downloads.mkdir(parents=True)
    archive = downloads / "2022q1.zip"
    sub_header = "adsh\tcik\tname\tform\tperiod\tfy\tfp\tfiled\n"
    sub_rows = "0001\t1\tAAA Inc\t10-Q\t20220331\t2022\tQ1\t20220420\n"
    num_header = "adsh\ttag\tversion\tcoreg\tddate\tqtrs\tuom\tvalue\tfootnote\n"
    num_rows = (
        "0001\tRevenues\tus-gaap/2022\t\t20220331\t1\tUSD\t100\t\n"
        "0001\tRevenues\tus-gaap/2022\tSUBSIDIARY\t20220331\t1\tUSD\t9999\t\n"
    )
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("sub.txt", sub_header + sub_rows)
        zf.writestr("num.txt", num_header + num_rows)
    client = SecFsdsClient(cache, "TestResearch test@example.com", periods=("2022q1",))
    eligible, records = client._extract_records(archive, {"0000000001": "AAA"}, __import__("datetime").date(2022, 4, 30))
    assert eligible == {"AAA"}
    assert [row["value"] for row in records if row["tag"] == "Revenues"] == [100.0]


def test_cumulative_derivation_never_crosses_fiscal_year() -> None:
    rows = [
        _record("2021-12-31", 1, 100, "Revenues"),
        {**_record("2022-03-31", 2, 250, "Revenues"), "fy": "2022", "fp": "Q2"},
    ]
    # The 2022 YTD fact must not subtract a 2021 fiscal-year quarter merely because
    # the dates are approximately 90 days apart.
    _, series = build_quarterly_series(rows, ["Revenues"], "revenue")
    assert ("2022-03-31", 150.0) not in series


def test_quality_audit_rejects_short_trailing_run_after_gap() -> None:
    from ible.collectors.sec_fsds import build_quarterly_series_detailed

    dates = [
        "2019-03-31", "2019-06-30", "2019-09-30", "2019-12-31",
        "2021-03-31", "2021-06-30", "2021-09-30",
    ]
    rows = [_record(date, 1, 100 + index * 10, "Revenues") for index, date in enumerate(dates)]
    _, series, audit = build_quarterly_series_detailed(rows, ["Revenues"], "revenue")
    assert len(series) == 3
    assert audit["quality_passed"] is False


def test_tag_selection_prefers_stable_series_over_longer_anomalous_series() -> None:
    from ible.collectors.sec_fsds import build_quarterly_series_detailed

    stable_dates = [
        "2020-03-31", "2020-06-30", "2020-09-30", "2020-12-31",
        "2021-03-31", "2021-06-30", "2021-09-30", "2021-12-31",
    ]
    noisy_dates = stable_dates + ["2022-03-31", "2022-06-30"]
    rows = [_record(date, 1, 100 + index * 5, "Revenues") for index, date in enumerate(stable_dates)]
    rows += [
        _record(date, 1, 1 if index < 4 else 1000 + index, "SalesRevenueNet")
        for index, date in enumerate(noisy_dates)
    ]
    tag, series, audit = build_quarterly_series_detailed(
        rows, ["Revenues", "SalesRevenueNet"], "revenue"
    )
    assert tag == "Revenues"
    assert len(series) == 8
    assert audit["quality_passed"] is True


def test_annual_proxy_fallback_recovers_safe_growth_when_quarters_are_sparse() -> None:
    from ible.collectors.sec_fsds import build_quarterly_series_detailed

    rows = [
        {**_record("2019-12-31", 4, 400, "Revenues", "2020-02-20"), "fy": "2019", "fp": "FY", "form": "10-K"},
        {**_record("2020-12-31", 4, 440, "Revenues", "2021-02-20"), "fy": "2020", "fp": "FY", "form": "10-K"},
        {**_record("2021-12-31", 4, 484, "Revenues", "2022-02-20"), "fy": "2021", "fp": "FY", "form": "10-K"},
    ]
    tag, series, audit = build_quarterly_series_detailed(rows, ["Revenues"], "revenue")
    assert tag == "Revenues"
    assert len(series) == 12
    assert audit["quality_passed"] is True
    assert audit["fallback_used"] is True
    assert audit["selection_method"] == "annual_flow_proxy_fallback"
    assert series[-1][1] == 121.0
