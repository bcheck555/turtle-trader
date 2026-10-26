from datetime import datetime
from decimal import Decimal as D
from zoneinfo import ZoneInfo

from turtle_trader.domain import ExecutionConfig, RiskConfig
from turtle_trader.risk import DailyLossGuard, size_entry


def test_size_respects_risk_cash_allocation_and_positions():
    config = RiskConfig(starting_cash=D(1000), risk_fraction=D(".1"),
                        allocation_fraction=D(1), max_positions=1)
    args = dict(equity=D(1000), cash=D(1000), price=D(100), stop=D(90),
                config=config, execution=ExecutionConfig(slippage_bps=D(0)))
    assert size_entry(**args).quantity == 10
    assert size_entry(**(args | {"cash": D(250)})).quantity == 2
    assert size_entry(**(args | {"occupied_positions": 1})).reason == "max_positions"
    assert size_entry(**(args | {"price": D(90)})).quantity == 0
    assert size_entry(**(args | {"daily_loss_latched": True})).quantity == 0


def test_costs_count_against_planned_risk_and_cash():
    decision = size_entry(
        equity=D(1000), cash=D(1000), price=D(100), stop=D(90),
        config=RiskConfig(starting_cash=D(1000), risk_fraction=D(".1"),
                          allocation_fraction=D(1)),
        execution=ExecutionConfig(slippage_bps=D(0), fee_per_order=D(1)))
    assert decision.quantity == 9
    assert decision.planned_risk == D(92)


def test_daily_loss_latches_through_recovery_and_resets_at_new_york_midnight():
    guard = DailyLossGuard(RiskConfig())
    ny = ZoneInfo("America/New_York")
    guard.roll_day(datetime(2025, 1, 2, 23, 59, tzinfo=ny), D(25000))
    assert guard.observe(D(24500))
    assert guard.observe(D(26000))
    guard.roll_day(datetime(2025, 1, 3, 0, 0, tzinfo=ny), D(26000))
    assert not guard.latched
    assert guard.baseline == D(26000)
