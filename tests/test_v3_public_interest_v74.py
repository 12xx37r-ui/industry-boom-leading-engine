import unittest
from datetime import date

from ible.v3_collectors import NaverSearchTrendCollector, Period


class _FakeClient:
    def request_json(self, url, **kwargs):
        groups = kwargs["payload"]["keywordGroups"]
        return {
            "results": [
                {"title": g["groupName"], "keywords": g["keywords"], "data": [
                    {"period": "2026-08-01", "ratio": 50.0},
                    {"period": "2026-08-02", "ratio": 75.0},
                ]}
                for g in groups
            ]
        }


class TestV74PublicInterest(unittest.TestCase):
    def test_naver_search_trend_parses_ratio_series(self):
        c = NaverSearchTrendCollector(_FakeClient(), "https://example.test", "id", "secret")
        out = c.search([
            {"groupName": "anchor", "keywords": ["반도체"]},
            {"groupName": "SMR", "keywords": ["SMR", "소형모듈원전"]},
        ], Period(date(2026,8,1), date(2026,8,2)))
        self.assertEqual(out["anchor"], [50.0,75.0])
        self.assertEqual(out["SMR"], [50.0,75.0])


if __name__ == "__main__":
    unittest.main()
