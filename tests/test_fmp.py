from __future__ import annotations

import json
from pathlib import Path

from ible.collectors.fmp import FmpClient, FmpError


def _annual(date: str, filed: str, **values):
    return {
        "date": date,
        "filingDate": filed,
        "calendarYear": date[:4],
        "period": "FY",
        **values,
    }


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


def test_fmp_api_key_validation(tmp_path: Path):
    client = FmpClient(tmp_path / "cache", "bad")
    try:
        client.validate_api_key()
    except FmpError as exc:
        assert "FMP_API_KEY" in str(exc)
    else:
        raise AssertionError("short API key should fail")


def test_fmp_sanitizes_accidental_apikey_prefix(tmp_path: Path):
    client = FmpClient(tmp_path / "cache", "?apikey=1234567890")
    assert client.api_key == "1234567890"


def test_fmp_request_uses_current_stable_endpoint_and_free_limit(tmp_path: Path):
    client = FmpClient(tmp_path / "cache", "1234567890", min_interval=0.25, annual_limit=99)
    client.session = _FakeSession([_FakeResponse(payload=[{"date": "2021-12-31"}])])
    rows = client._request_rows("income-statement", "AAPL")
    assert rows
    url, kwargs = client.session.calls[0]
    assert url == "https://financialmodelingprep.com/stable/income-statement"
    assert "/api/v3/" not in url
    assert kwargs["params"]["apikey"] == "1234567890"
    assert kwargs["params"]["symbol"] == "AAPL"
    assert kwargs["params"]["period"] == "annual"
    assert kwargs["params"]["limit"] == 5


def test_fmp_preflight_is_fail_soft_and_does_not_raise(tmp_path: Path):
    client = FmpClient(tmp_path / "cache", "1234567890", min_interval=0.25)
    client.session = _FakeSession(
        [
            _FakeResponse(status_code=402, text="limit must be between 0 and 5"),
            _FakeResponse(status_code=403, text="subscription"),
            _FakeResponse(status_code=403, text="subscription"),
        ]
    )
    status = client.prepare_subset(["AAA", "BBB"])
    assert status["status"] == "UNAVAILABLE"
    assert status["available"] == 0
    assert len(client.session.calls) == 3
    disk = json.loads(client.status_path.read_text(encoding="utf-8"))
    assert disk["status"] == "UNAVAILABLE"


def test_fmp_prepare_subset_uses_cache_without_network(tmp_path: Path):
    client = FmpClient(tmp_path / "cache", "1234567890")
    (client.subset_dir / "AAA.json").write_text(
        json.dumps({"ticker": "AAA", "income_statement": [], "cash_flow_statement": []}),
        encoding="utf-8",
    )
    status = client.prepare_subset(["AAA"])
    assert status["status"] == "COMPLETE"
    assert status["cached"] == 1
    assert status["downloaded"] == 0
    assert client.preflight_result is None


def test_fmp_builds_eight_point_bridge_from_fy_value_and_growth(tmp_path: Path):
    client = FmpClient(tmp_path / "cache", "1234567890")
    payload = {
        "ticker": "AAA",
        "income_statement": [
            _annual(
                "2021-12-31",
                "2022-02-15",
                revenue=1200,
                grossProfit=480,
                operatingIncome=120,
                researchAndDevelopmentExpenses=60,
            ),
            _annual(
                "2022-12-31",
                "2023-02-15",
                revenue=9999,
                grossProfit=9999,
                operatingIncome=9999,
                researchAndDevelopmentExpenses=9999,
            ),
        ],
        "cash_flow_statement": [
            _annual("2021-12-31", "2022-02-15", capitalExpenditure=-240),
        ],
        "financial_growth": [
            {
                "date": "2021-12-31",
                "calendarYear": "2021",
                "period": "FY",
                "growthRevenue": 0.20,
                "growthGrossProfit": 0.20,
                "growthOperatingIncome": 0.20,
                "growthResearchAndDevelopmentExpenses": 0.20,
                "growthCapitalExpenditure": 0.20,
            }
        ],
    }
    (client.subset_dir / "AAA.json").write_text(json.dumps(payload), encoding="utf-8")
    series, errors = client.load_series(["AAA"], "2022-04-30")
    assert not errors
    assert len(series["revenue"]["AAA"]) == 8
    assert series["revenue"]["AAA"][-1][0] == "2021-12-31"
    assert round(series["revenue"]["AAA"][-1][1], 6) == 300.0
    assert round(series["revenue"]["AAA"][0][1], 6) == 250.0
    assert round(series["capex"]["AAA"][-1][1], 6) == 60.0
    assert all(v >= 0 for _, v in series["capex"]["AAA"])


def test_fmp_excludes_statement_filed_after_cutoff(tmp_path: Path):
    client = FmpClient(tmp_path / "cache", "1234567890")
    payload = {
        "ticker": "AAA",
        "income_statement": [
            _annual("2021-12-31", "2022-06-01", revenue=1200),
        ],
        "cash_flow_statement": [],
        "financial_growth": [
            {"date": "2021-12-31", "calendarYear": "2021", "growthRevenue": 0.2}
        ],
    }
    (client.subset_dir / "AAA.json").write_text(json.dumps(payload), encoding="utf-8")
    series, errors = client.load_series(["AAA"], "2022-04-30")
    assert errors["AAA"] == "no annual statement filed by holdout cutoff"
    assert series["revenue"]["AAA"] == []


def test_fmp_prepare_subset_downloads_three_stable_payloads(tmp_path: Path):
    client = FmpClient(tmp_path / "cache", "1234567890", min_interval=0.25)
    annual = [_annual("2021-12-31", "2022-02-15", revenue=100)]
    growth = [{"date": "2021-12-31", "calendarYear": "2021", "growthRevenue": 0.1}]
    # Three preflight calls followed by three calls for AAA.
    client.session = _FakeSession(
        [
            _FakeResponse(payload=annual),
            _FakeResponse(payload=[_annual("2021-12-31", "2022-02-15", capitalExpenditure=-10)]),
            _FakeResponse(payload=growth),
            _FakeResponse(payload=annual),
            _FakeResponse(payload=[_annual("2021-12-31", "2022-02-15", capitalExpenditure=-10)]),
            _FakeResponse(payload=growth),
        ]
    )
    status = client.prepare_subset(["AAA"])
    assert status["status"] == "COMPLETE"
    assert status["downloaded"] == 1
    payload = json.loads((client.subset_dir / "AAA.json").read_text(encoding="utf-8"))
    assert payload["period_mode"] == "annual"
    assert payload["annual_limit"] == 5
    assert payload["financial_growth"][0]["growthRevenue"] == 0.1
    assert all("/stable/" in call[0] for call in client.session.calls)
