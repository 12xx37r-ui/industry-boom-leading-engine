from ible.analytics.scoring import build_metric_signal, stage_for


def _series(values):
    return [(f"202{i//4}Q{i%4}", value) for i, value in enumerate(values)]


def test_growth_signal_rewards_acceleration_and_breadth():
    signal = build_metric_signal(
        "test",
        {
            "A": _series([10, 11, 12, 13, 14, 16, 19, 23, 28]),
            "B": _series([20, 21, 22, 23, 24, 27, 31, 36, 42]),
        },
    )
    assert signal.score > 60
    assert signal.breadth == 100


def test_stage_requires_confidence():
    assert stage_for(85, 40, 90, 90) == "INSUFFICIENT_DATA"
    assert stage_for(85, 90, 80, 80) == "PRE_BOOM"
