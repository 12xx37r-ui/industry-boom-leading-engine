from ible.backtest import aggregate_results, evaluate_scenario


def _row(theme_id: str, score: float, early: float, stage: str = "EARLY_ACCUMULATION"):
    return {
        "theme_id": theme_id,
        "boom_score": score,
        "early_signal_score": early,
        "commercial_realization_score": 45,
        "cross_confirmation_score": 60,
        "stage": stage,
        "data_confidence": 60,
        "coverage": {"usable_companies": 3, "company_coverage": 0.75},
    }


def test_positive_scenario_passes_when_target_is_early_and_high_ranked():
    scenario = {
        "id": "P",
        "name": "positive",
        "label": "positive",
        "as_of": "2020-01-01",
        "target_theme_id": "TARGET",
        "criteria": {"rank_max": 2, "score_min": 60, "early_signal_min": 60},
    }
    result = evaluate_scenario(scenario, [_row("TARGET", 64, 63), _row("OTHER", 61, 60)])
    assert result["status"] == "PASSED"
    assert result["alert_triggered"] is True


def test_negative_scenario_fails_on_strong_false_alarm():
    scenario = {
        "id": "N",
        "name": "negative",
        "label": "negative",
        "as_of": "2020-01-01",
        "target_theme_id": "TARGET",
        "criteria": {"strong_alert_rank_max": 3, "strong_alert_score_min": 60, "strong_alert_early_min": 58},
    }
    result = evaluate_scenario(scenario, [_row("TARGET", 65, 62)])
    assert result["status"] == "FAILED"
    assert result["alert_triggered"] is True


def test_aggregate_requires_recall_and_low_false_alarm():
    rows = [
        {"status": "PASSED", "label": "positive", "passed": True, "alert_triggered": True},
        {"status": "PASSED", "label": "positive", "passed": True, "alert_triggered": True},
        {"status": "FAILED", "label": "positive", "passed": False, "alert_triggered": False},
        {"status": "PASSED", "label": "negative", "passed": True, "alert_triggered": False},
        {"status": "PASSED", "label": "negative", "passed": True, "alert_triggered": False},
    ]
    result = aggregate_results(rows)
    assert result["stage2_passed"] is True
    assert result["investment_use_allowed"] is False


def test_positive_scenario_accepts_capital_led_accumulation_stage():
    scenario = {
        "id": "EV",
        "name": "capital-led positive",
        "label": "positive",
        "as_of": "2019-12-31",
        "target_theme_id": "TARGET",
        "criteria": {"rank_max": 4, "score_min": 55, "early_signal_min": 54},
    }
    row = _row("TARGET", 57.4, 58.6, stage="CAPITAL_LED_ACCUMULATION")
    result = evaluate_scenario(scenario, [row, _row("OTHER", 56, 56)])
    assert result["status"] == "PASSED"
    assert result["alert_triggered"] is True
