from ible.analytics.exposure_scoring import build_exposure_weighted_signal


def series(mult=1.0):
    return [(f'202{i//4}-{(i%4+1)*3:02d}-28', mult*(100+i*10)) for i in range(12)]


def test_low_exposure_company_is_excluded():
    profiles = {
        'PURE': {'exposure': 0.9, 'confidence': 0.9, 'evidence': 'pure'},
        'NOISE': {'exposure': 0.1, 'confidence': 1.0, 'evidence': 'noise'},
    }
    signal = build_exposure_weighted_signal('x', {'PURE': series(), 'NOISE': series(-10)}, profiles, 0.3)
    assert signal.raw['eligible_company_count'] == 1
    assert 'PURE' in signal.raw['companies']
    assert 'NOISE' not in signal.raw['companies']


def test_concentration_warning_applies():
    profiles = {'A': {'exposure': 0.9, 'confidence': 0.9}, 'B': {'exposure': 0.31, 'confidence': 0.3}}
    signal = build_exposure_weighted_signal('x', {'A': series(), 'B': series()}, profiles, 0.3)
    assert any('집중도' in w for w in signal.warnings)


def test_exposure_signal_robustly_limits_single_company_outlier() -> None:
    from ible.analytics.exposure_scoring import build_exposure_weighted_signal

    dates = [
        "2020-03-31", "2020-06-30", "2020-09-30", "2020-12-31",
        "2021-03-31", "2021-06-30", "2021-09-30", "2021-12-31",
    ]
    normal = [100, 100, 100, 100, 110, 110, 110, 110]
    extreme = [1, 1, 1, 1, 1000, 1000, 1000, 1000]
    company_series = {
        "A": list(zip(dates, normal, strict=True)),
        "B": list(zip(dates, normal, strict=True)),
        "C": list(zip(dates, normal, strict=True)),
        "D": list(zip(dates, normal, strict=True)),
        "E": list(zip(dates, extreme, strict=True)),
    }
    profiles = {
        ticker: {"exposure": 1.0, "confidence": 1.0, "evidence": "test"}
        for ticker in company_series
    }
    signal = build_exposure_weighted_signal("us_exposure_revenue", company_series, profiles)
    assert signal.raw["raw_weighted_yoy_mean"] > 100
    assert signal.raw["weighted_yoy"] < 1
    assert signal.score < 80


def test_margin_signal_shrinks_low_coverage_extreme() -> None:
    from ible.analytics.exposure_scoring import build_exposure_weighted_margin_signal

    dates = ["2020-03-31", "2020-06-30", "2020-09-30", "2020-12-31", "2021-03-31", "2021-06-30"]
    revenue = {"A": [(date, 100.0) for date in dates]}
    profit = {"A": [(date, value) for date, value in zip(dates, [10, 10, 10, 10, 40, 40], strict=True)]}
    profiles = {
        "A": {"exposure": 1.0, "confidence": 1.0},
        "B": {"exposure": 1.0, "confidence": 1.0},
        "C": {"exposure": 1.0, "confidence": 1.0},
        "D": {"exposure": 1.0, "confidence": 1.0},
    }
    signal = build_exposure_weighted_margin_signal(revenue, profit, profiles)
    assert signal.coverage == 0.25
    assert 50 < signal.score < 70


def test_margin_signal_rejects_profit_above_revenue() -> None:
    from ible.analytics.exposure_scoring import build_exposure_weighted_margin_signal

    dates = ["2020-03-31", "2020-06-30", "2020-09-30", "2020-12-31", "2021-03-31", "2021-06-30"]
    revenue = {"A": [(date, 100.0) for date in dates]}
    profit = {"A": [(date, 150.0) for date in dates]}
    profiles = {"A": {"exposure": 1.0, "confidence": 1.0}}
    signal = build_exposure_weighted_margin_signal(revenue, profit, profiles)
    assert signal.coverage == 0.0
    assert signal.score == 50.0
    assert signal.raw["rejected_implausible_margin_points"] == len(dates)
