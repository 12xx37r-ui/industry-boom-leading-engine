from ible.global_validation import _balanced_adoption_layer, _balanced_stage


def test_balanced_layer_rewards_infrastructure_with_viable_demand() -> None:
    result = _balanced_adoption_layer(
        capex_score=64.1,
        rd_score=56.2,
        revenue_score=44.8,
        operating_score=54.0,
        research_score=48.1,
        economic_scale_score=51.7,
        capital_without_demand_gap=3.4,
    )
    assert result["quality_score"] >= 70
    assert "INFRASTRUCTURE_CAPITAL" in result["confirmed_pathways"]
    assert _balanced_stage(
        result["quality_score"], 85, result["confirmed_pathways"], 49, 45
    ) == "EARLY_ACCUMULATION"


def test_balanced_layer_rewards_durable_demand_path() -> None:
    result = _balanced_adoption_layer(
        capex_score=43.0,
        rd_score=57.0,
        revenue_score=55.0,
        operating_score=51.0,
        research_score=47.0,
        economic_scale_score=66.0,
        capital_without_demand_gap=0.0,
    )
    assert result["quality_score"] >= 65
    assert "DEMAND_DURABILITY" in result["confirmed_pathways"]


def test_balanced_layer_rewards_innovation_only_when_scaled() -> None:
    scaled = _balanced_adoption_layer(
        capex_score=56.0,
        rd_score=68.0,
        revenue_score=49.0,
        operating_score=45.0,
        research_score=61.0,
        economic_scale_score=60.0,
        capital_without_demand_gap=1.0,
    )
    unscaled = _balanced_adoption_layer(
        capex_score=56.0,
        rd_score=68.0,
        revenue_score=39.0,
        operating_score=39.0,
        research_score=61.0,
        economic_scale_score=34.0,
        capital_without_demand_gap=12.0,
    )
    assert "INNOVATION_SCALE" in scaled["confirmed_pathways"]
    assert "INNOVATION_SCALE" not in unscaled["confirmed_pathways"]
    assert scaled["quality_score"] > unscaled["quality_score"] + 20


def test_capital_only_bubble_does_not_enter_alert_stage() -> None:
    result = _balanced_adoption_layer(
        capex_score=71.0,
        rd_score=67.0,
        revenue_score=48.0,
        operating_score=38.0,
        research_score=65.0,
        economic_scale_score=40.0,
        capital_without_demand_gap=9.0,
    )
    assert result["confirmed_pathways"] == []
    assert result["viability_penalty"] > 0
    assert result["imbalance_penalty"] > 0
    assert _balanced_stage(
        result["quality_score"], 94, result["confirmed_pathways"], 51, 48
    ) in {"WATCH", "NO_SIGNAL"}


def test_low_confidence_overrides_pathway() -> None:
    assert _balanced_stage(80, 40, ["DEMAND_DURABILITY"], 60, 60) == "INSUFFICIENT_DATA"
