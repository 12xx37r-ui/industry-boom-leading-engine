from ible.analytics.sec_metrics import quarterly_flow


def test_quarterly_flow_filters_by_filed_date():
    facts = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {"start": "2021-01-01", "end": "2021-03-31", "val": 100, "filed": "2021-05-01", "form": "10-Q"},
                            {"start": "2022-01-01", "end": "2022-03-31", "val": 130, "filed": "2022-05-01", "form": "10-Q"},
                            {"start": "2023-01-01", "end": "2023-03-31", "val": 999, "filed": "2023-05-01", "form": "10-Q"},
                        ]
                    }
                }
            }
        }
    }
    tag, rows = quarterly_flow(facts, ["Revenues"], "2022-12-31")
    assert tag == "Revenues"
    assert rows[-1][1] == 130
    assert all(date < "2023-01-01" for date, _ in rows)
