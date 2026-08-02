from ible.analytics.dart_metrics import extract_disclosure_amount


def test_extract_contract_amount_in_won():
    text = "단일판매ㆍ공급계약체결 계약금액(원) 123,456,789,000 최근매출액 대비 12.4%"
    amount, meta = extract_disclosure_amount(text, "CONTRACT_EVENT")
    assert amount == 123_456_789_000
    assert meta["status"] == "FOUND"


def test_extract_capital_amount_with_eok_unit():
    text = "신규시설투자 결정 투자금액 2,350 억원 투자기간 2026년"
    amount, meta = extract_disclosure_amount(text, "CAPITAL_EVENT")
    assert amount == 235_000_000_000
    assert meta["unit"] == "억원"


def test_percentage_is_not_amount():
    text = "계약금액 최근매출액 대비 25.7%"
    amount, meta = extract_disclosure_amount(text, "CONTRACT_EVENT")
    assert amount is None
    assert meta["status"] == "NOT_FOUND"
