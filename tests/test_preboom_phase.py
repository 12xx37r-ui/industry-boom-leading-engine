from ible.analytics.scoring import build_dart_theme_result
from ible.models import Signal


def s(name, score, breadth=50, persistence=50, coverage=1.0, amount_coverage=None):
    raw = {}
    if amount_coverage is not None:
        raw["amount_coverage"] = amount_coverage
    return Signal(
        name=name,
        score=score,
        breadth=breadth,
        persistence=persistence,
        coverage=coverage,
        raw=raw,
    )


def build(signals):
    return build_dart_theme_result(
        theme_id="TEST",
        theme_name="테스트",
        as_of="2022-10-31",
        signals=signals,
        requested_companies=4,
        usable_companies=4,
        invalidations=[],
    )


def test_early_accumulation_rewards_research_capex_revenue_sequence():
    result = build({
        "capital_events": s("capital_count", 35, breadth=0, persistence=0),
        "capital_amounts": s("capital", 35, breadth=0, persistence=0, amount_coverage=0.5),
        "supply_contracts": s("contract_count", 32, breadth=0, persistence=25),
        "contract_amounts": s("contracts", 35, breadth=0, persistence=25, amount_coverage=0.5),
        "cashflow_capex": s("cashflow", 58, breadth=75, persistence=25),
        "revenue": s("revenue", 56, breadth=75, persistence=62.5),
        "operating_margin": s("margin", 48, breadth=50, persistence=50),
        "research_momentum": s("research", 80, breadth=100, persistence=100),
    })
    assert result.stage == "EARLY_ACCUMULATION"
    assert result.early_signal_score >= 60
    assert result.boom_score >= 60
    assert result.commercial_realization_score < 55


def test_mature_commercial_boom_is_not_mistaken_for_hidden_preboom():
    result = build({
        "capital_events": s("capital_count", 65, breadth=75, persistence=100),
        "capital_amounts": s("capital", 64, breadth=75, persistence=100, amount_coverage=1.0),
        "supply_contracts": s("contract_count", 60, breadth=50, persistence=100),
        "contract_amounts": s("contracts", 60, breadth=50, persistence=100, amount_coverage=1.0),
        "cashflow_capex": s("cashflow", 60, breadth=100, persistence=50),
        "revenue": s("revenue", 78, breadth=75, persistence=87.5),
        "operating_margin": s("margin", 66, breadth=75, persistence=50),
        "research_momentum": s("research", 45, breadth=50, persistence=50),
    })
    assert result.commercial_realization_score >= 64
    assert result.stage in {"TRANSITION", "COMMERCIAL_BOOM"}
    assert result.transition_gap_score < 50


def test_research_only_signal_fails_cross_confirmation():
    result = build({
        "capital_events": s("capital_count", 30, breadth=0, persistence=0),
        "capital_amounts": s("capital", 30, breadth=0, persistence=0, amount_coverage=0.5),
        "supply_contracts": s("contract_count", 30, breadth=0, persistence=0),
        "contract_amounts": s("contracts", 30, breadth=0, persistence=0, amount_coverage=0.5),
        "cashflow_capex": s("cashflow", 35, breadth=0, persistence=0),
        "revenue": s("revenue", 40, breadth=0, persistence=0),
        "operating_margin": s("margin", 50, breadth=0, persistence=0),
        "research_momentum": s("research", 90, breadth=100, persistence=100),
    })
    assert result.cross_confirmation_score < 52
    assert result.stage != "EARLY_ACCUMULATION"


def test_capital_led_accumulation_detects_hard_asset_boom_before_research_accelerates():
    result = build({
        "capital_events": s("capital_count", 56, breadth=25, persistence=100),
        "capital_amounts": s("capital", 54.5, breadth=25, persistence=100, amount_coverage=1.0),
        "supply_contracts": s("contract_count", 55, breadth=25, persistence=100),
        "contract_amounts": s("contracts", 55, breadth=25, persistence=100, amount_coverage=1.0),
        "cashflow_capex": s("cashflow", 70, breadth=100, persistence=75),
        "revenue": s("revenue", 59, breadth=100, persistence=75),
        "operating_margin": s("margin", 43, breadth=25, persistence=50),
        "research_momentum": s("research", 44, breadth=50, persistence=50),
    })
    assert result.stage == "CAPITAL_LED_ACCUMULATION"
    assert result.coverage["leading_pathways"]["dominant_path"] == "CAPITAL_LED"
    assert result.early_signal_score >= 56
    assert result.cross_confirmation_score >= 54


def test_revenue_hype_without_capex_cross_confirmation_does_not_become_capital_led():
    result = build({
        "capital_events": s("capital_count", 31, breadth=0, persistence=25),
        "capital_amounts": s("capital", 30, breadth=0, persistence=25, amount_coverage=0.6),
        "supply_contracts": s("contract_count", 62, breadth=50, persistence=50),
        "contract_amounts": s("contracts", 63, breadth=50, persistence=50, amount_coverage=0.6),
        "cashflow_capex": s("cashflow", 43, breadth=25, persistence=25),
        "revenue": s("revenue", 77, breadth=50, persistence=75),
        "operating_margin": s("margin", 26, breadth=25, persistence=25),
        "research_momentum": s("research", 66, breadth=100, persistence=100),
    })
    assert result.cross_confirmation_score < 54
    assert result.stage != "CAPITAL_LED_ACCUMULATION"
