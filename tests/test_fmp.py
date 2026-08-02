from __future__ import annotations

import json
from pathlib import Path

from ible.collectors.fmp import FmpClient, FmpError


def _row(date: str, filed: str, period: str, **values):
    return {"date": date, "filingDate": filed, "period": period, **values}


def test_fmp_load_series_filters_by_filing_date_and_normalizes_capex(tmp_path: Path):
    client = FmpClient(tmp_path / "cache", "1234567890")
    payload = {
        "ticker": "AAA",
        "income_statement": [
            _row("2020-03-31", "2020-05-01", "Q1", revenue=100, grossProfit=40, operatingIncome=10, researchAndDevelopmentExpenses=5),
            _row("2020-06-30", "2020-08-01", "Q2", revenue=110, grossProfit=42, operatingIncome=11, researchAndDevelopmentExpenses=6),
            _row("2020-09-30", "2020-11-01", "Q3", revenue=120, grossProfit=43, operatingIncome=12, researchAndDevelopmentExpenses=7),
            _row("2020-12-31", "2021-02-01", "Q4", revenue=130, grossProfit=45, operatingIncome=13, researchAndDevelopmentExpenses=8),
            _row("2021-03-31", "2021-05-01", "Q1", revenue=150, grossProfit=55, operatingIncome=15, researchAndDevelopmentExpenses=9),
            _row("2021-06-30", "2021-08-01", "Q2", revenue=160, grossProfit=58, operatingIncome=16, researchAndDevelopmentExpenses=10),
            _row("2021-09-30", "2021-11-01", "Q3", revenue=170, grossProfit=60, operatingIncome=17, researchAndDevelopmentExpenses=11),
            _row("2021-12-31", "2022-02-01", "Q4", revenue=999, grossProfit=999, operatingIncome=999, researchAndDevelopmentExpenses=999),
        ],
        "cash_flow_statement": [
            _row("2020-03-31", "2020-05-01", "Q1", capitalExpenditure=-10),
            _row("2020-06-30", "2020-08-01", "Q2", capitalExpenditure=-11),
            _row("2020-09-30", "2020-11-01", "Q3", capitalExpenditure=-12),
            _row("2020-12-31", "2021-02-01", "Q4", capitalExpenditure=-13),
            _row("2021-03-31", "2021-05-01", "Q1", capitalExpenditure=-15),
            _row("2021-06-30", "2021-08-01", "Q2", capitalExpenditure=-16),
            _row("2021-09-30", "2021-11-01", "Q3", capitalExpenditure=-17),
        ],
    }
    path = client.subset_dir / "AAA.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    series, errors = client.load_series(["AAA"], "2021-12-31")
    assert not errors
    assert series["revenue"]["AAA"][-1] == ("2021-09-30", 170.0)
    assert series["capex"]["AAA"][-1] == ("2021-09-30", 17.0)
    assert all(value >= 0 for _, value in series["capex"]["AAA"])


def test_fmp_prepare_subset_uses_cache(tmp_path: Path):
    client = FmpClient(tmp_path / "cache", "1234567890")
    (client.subset_dir / "AAA.json").write_text(
        json.dumps({"ticker": "AAA", "income_statement": [], "cash_flow_statement": []}),
        encoding="utf-8",
    )
    status = client.prepare_subset(["AAA"])
    assert status["status"] == "COMPLETE"
    assert status["cached"] == 1
    assert status["downloaded"] == 0


def test_fmp_api_key_validation(tmp_path: Path):
    client = FmpClient(tmp_path / "cache", "bad")
    try:
        client.validate_api_key()
    except FmpError as exc:
        assert "FMP_API_KEY" in str(exc)
    else:
        raise AssertionError("short API key should fail")


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def test_fmp_sanitizes_accidental_apikey_prefix(tmp_path: Path):
    client = FmpClient(tmp_path / "cache", "?apikey=1234567890")
    assert client.api_key == "1234567890"


def test_fmp_request_uses_apikey_query_parameter(tmp_path: Path):
    client = FmpClient(tmp_path / "cache", "1234567890", min_interval=0.25)
    session = _FakeSession([_FakeResponse(payload=[{"date": "2021-09-30"}])])
    client.session = session
    rows = client._request_rows(
        "https://financialmodelingprep.com/stable/income-statement",
        {"symbol": "AAPL", "period": "quarter", "limit": 20},
        purpose="test",
    )
    assert rows
    _, kwargs = session.calls[0]
    assert kwargs["params"]["apikey"] == "1234567890"
    assert kwargs["params"]["symbol"] == "AAPL"
    assert kwargs["params"]["limit"] == 20


def test_fmp_preflight_records_provider_error_without_cohort_loop(tmp_path: Path):
    client = FmpClient(tmp_path / "cache", "1234567890", min_interval=0.25)
    client.session = _FakeSession([
        _FakeResponse(status_code=401, text='{"Error Message":"Invalid API Key"}'),
        _FakeResponse(status_code=401, text='{"Error Message":"Invalid API Key"}'),
    ])
    try:
        client.prepare_subset(["AAA", "BBB"])
    except FmpError as exc:
        assert "preflight" in str(exc).lower()
    else:
        raise AssertionError("preflight should fail")
    status = json.loads(client.status_path.read_text(encoding="utf-8"))
    assert status["status"] == "PREFLIGHT_FAILED"
    assert status["requested"] == 0
    assert len(client.session.calls) == 2
