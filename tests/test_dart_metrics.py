import datetime as dt

from ible.analytics.dart_metrics import (
    build_quarterly_financial_series,
    classify_disclosure,
    report_available,
)


def row(stock, name, report, amount, add_amount=None, fs_div="CFS"):
    return {
        "stock_code": stock,
        "account_nm": name,
        "reprt_code": report,
        "thstrm_amount": str(amount),
        "thstrm_add_amount": str(add_amount) if add_amount is not None else None,
        "fs_div": fs_div,
    }


def test_quarterly_derivation_from_cumulative_accounts():
    rows = {
        (2022, "11013"): [row("005930", "매출액", "11013", 100, 100)],
        (2022, "11012"): [row("005930", "매출액", "11012", 120, 220)],
        (2022, "11014"): [row("005930", "매출액", "11014", 140, 360)],
        (2022, "11011"): [row("005930", "매출액", "11011", 520, 520)],
    }
    revenue, _ = build_quarterly_financial_series(rows, ["005930"])
    assert [value for _, value in revenue["005930"]] == [100.0, 120.0, 140.0, 160.0]


def test_report_point_in_time_availability():
    assert report_available(dt.date(2022, 10, 31), 2022, "11012")
    assert not report_available(dt.date(2022, 10, 31), 2022, "11014")
    assert report_available(dt.date(2022, 10, 31), 2021, "11011")


def test_disclosure_classification():
    assert classify_disclosure("신규시설투자등") == "CAPITAL_EVENT"
    assert classify_disclosure("단일판매ㆍ공급계약체결") == "CONTRACT_EVENT"
    assert classify_disclosure("분기보고서") is None
