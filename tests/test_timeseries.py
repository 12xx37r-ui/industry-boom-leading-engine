from ible.analytics.timeseries import clamp, robust_z, safe_growth, slope


def test_safe_growth_handles_negative_and_zero():
    assert safe_growth(120, 100) == 0.2
    assert safe_growth(10, 0) is None
    assert safe_growth(-80, -100) == 0.2


def test_robust_z_detects_high_latest():
    assert robust_z([10, 10, 11, 10, 12, 30]) > 1


def test_slope_and_clamp():
    assert slope([1, 2, 3, 4]) > 0
    assert clamp(120) == 100
    assert clamp(-1) == 0
