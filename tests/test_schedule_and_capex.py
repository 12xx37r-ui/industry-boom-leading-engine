import datetime as dt

from ible.analytics.dart_metrics import (
    annual_capex_from_full_accounts,
    enrich_disclosure_amount,
    event_amount_series,
    event_series,
    extract_event_schedule,
)
from ible.analytics.scoring import build_annual_capex_signal


def test_extract_contract_schedule():
    text = "계약금액 100억원 계약기간 시작일 2021년 01월 01일 종료일 2023년 12월 31일"
    result = extract_event_schedule(text, "CONTRACT_EVENT")
    assert result["status"] == "FOUND"
    assert result["start_date"] == "2021-01-01"
    assert result["end_date"] == "2023-12-31"


def test_schedule_aware_event_allocation():
    row = enrich_disclosure_amount(
        {"report_nm": "단일판매ㆍ공급계약체결", "rcept_dt": "20210101"},
        "계약금액 120억원 계약기간 2021년 01월 01일 2021년 12월 31일",
    )
    disclosures = {"000001": [row]}
    counts = event_series(disclosures, ["000001"], dt.date(2021, 12, 31), "CONTRACT_EVENT", periods=4, days_per_period=91)
    assert all(value > 0 for _, value in counts["000001"])
    amounts, quality = event_amount_series(disclosures, ["000001"], dt.date(2021, 12, 31), "CONTRACT_EVENT", periods=4, days_per_period=91)
    assert sum(value for _, value in amounts["000001"]) > 10_000_000_000
    assert quality["schedule_coverage"] == 1.0


def test_annual_capex_primary_account():
    rows = [
        {
            "sj_div": "CF",
            "account_id": "ifrs-full_PurchaseOfPropertyPlantAndEquipment",
            "account_nm": "유형자산의 취득",
            "thstrm_amount": "-12,345,000,000",
        }
    ]
    amount, meta = annual_capex_from_full_accounts(rows)
    assert amount == 12_345_000_000
    assert meta["status"] == "FOUND_PRIMARY"


def test_annual_capex_signal_rising_breadth():
    series = {
        "A": [("2020-12-31", 0.05), ("2021-12-31", 0.07), ("2022-12-31", 0.10)],
        "B": [("2020-12-31", 0.02), ("2021-12-31", 0.03), ("2022-12-31", 0.04)],
    }
    signal = build_annual_capex_signal("capex", series)
    assert signal.score > 50
    assert signal.breadth == 100.0
    assert signal.coverage == 1.0
