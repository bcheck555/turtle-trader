from datetime import UTC, datetime
from decimal import Decimal

import pytest

from turtle_trader.domain import RiskConfig
from turtle_trader.risk import SizingCapital, unit_limit_reason, unit_shares, validate_groups

D = Decimal
AT = datetime(2026, 1, 5, tzinfo=UTC)


def test_faith_drawdown_examples_recovery_and_year_reset() -> None:
    sizing = SizingCapital(D(1000000))
    sizing.observe(AT, D(1000000))
    sizing.observe(AT, D(900000))
    assert sizing.capital == 800000
    sizing.observe(AT, D(820000))
    assert sizing.capital == 640000
    sizing.observe(AT, D(1000000))
    assert sizing.capital == 1000000
    sizing.observe(AT.replace(year=2027), D(1200000))
    assert sizing.capital == sizing.baseline == 1200000


def test_drawdown_gap_multiple_thresholds_and_insolvency() -> None:
    sizing = SizingCapital(D(1000000))
    sizing.observe(AT, D(1000000))
    sizing.observe(AT, D(820000))
    assert sizing.capital == 640000
    sizing.observe(AT, D(-1))
    assert sizing.capital == 0


def test_drawdown_beyond_geometric_threshold_limit_clamps_without_looping() -> None:
    sizing = SizingCapital(D(1000000))
    sizing.observe(AT, D(1000000))
    sizing.observe(AT, D(400000))
    assert sizing.capital == 0
    sizing.observe(AT, D(1000000))
    assert sizing.capital == 1000000


def test_1n_unit_sizing_not_stop_risk_sizing() -> None:
    assert unit_shares(D(1000000), D(2), RiskConfig()) == 5000
    assert unit_shares(D(10), D(100), RiskConfig()) == 0


def test_position_sizing_configuration_is_explicit_and_validated() -> None:
    from turtle_trader.config import deviations, load_config

    config = load_config(overrides=['risk.position_sizing="available_cash"', 'risk.max_units=1'])
    assert config.risk.position_sizing == "available_cash"
    assert any("position_sizing" in change for change in deviations(config))
    with pytest.raises(ValueError, match="position_sizing"):
        load_config(overrides=['risk.position_sizing="all_in"'])


def test_limits_include_reserved_units_and_directions() -> None:
    config = RiskConfig()
    assert unit_limit_reason(config, "ABC", 1, [("ABC", 1)] * 4) == "symbol_unit_limit"
    assert unit_limit_reason(config, "ABC", 1, [("XYZ", 1)] * 6) == "close_correlation_limit"
    assert unit_limit_reason(config, "ABC", -1, [("XYZ", 1)] * 12) is None
    groups = RiskConfig(close_groups=(("ABC",), ("XYZ",)),
                        loose_groups=(("ABC",), ("XYZ",)))
    assert unit_limit_reason(groups, "ABC", 1, [("XYZ", 1)] * 12) == "direction_unit_limit"


def test_groups_must_cover_dataset() -> None:
    with pytest.raises(ValueError, match="cover exactly"):
        validate_groups(RiskConfig(close_groups=(("ABC",),)), {"ABC", "XYZ"})
