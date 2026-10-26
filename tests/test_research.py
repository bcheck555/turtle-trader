import pytest

from turtle_trader.research import research_warnings, strategy_warnings, validate_price_basis


def test_adjusted_basis_disclosure_and_validation() -> None:
    assert not research_warnings("action_free")
    assert "vendor-adjusted" in research_warnings("adjusted_research")[0]
    with pytest.raises(ValueError, match="unknown price basis"):
        validate_price_basis("raw")


def test_alternative_exit_disclosed() -> None:
    assert not strategy_warnings("turtle_channel")
    assert "protective Turtle stops" in strategy_warnings("chandelier")[0]
    assert "Custom exit" in strategy_warnings("custom")[0]
