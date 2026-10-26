from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from turtle_trader import backtest
from turtle_trader.domain import Bar, ExecutionConfig, OrderIntent, RiskConfig, StrategyConfig
from turtle_trader.domain import StrategyEvents as Event
from turtle_trader.execution import OfflineBroker
from turtle_trader.portfolio import Portfolio

START = datetime(2025, 1, 2, 15, 0, tzinfo=UTC)
RISK = RiskConfig(starting_cash=D(1000), risk_fraction=D(".1"),
                  allocation_fraction=D(1), daily_loss_fraction=D(".9"))
EXECUTION = ExecutionConfig(slippage_bps=D(0))


class AllHours:
    def eligible(self, at, market_hours="regular"):
        return True


def bar(index, opening=100, low=99, close=100, volume=1000, symbol="A"):
    time = START + timedelta(minutes=index * 5)
    return Bar(symbol, time, time + timedelta(minutes=5), opening,
               max(opening, close) + 1, low, close, volume)


def replay(monkeypatch, bars, changes, **kwargs):
    class ScriptedStrategy:
        def __init__(self, config):
            pass

        def on_bar(self, current):
            index = int((current.time - START).total_seconds() // 300)
            fields = changes.get((current.symbol, index), changes.get(index, {}))
            return Event(current.symbol, current.time, current.time_close,
                         **({"held": True, "initial_stop": 90, "active_stop": 90} | fields))

    monkeypatch.setattr(backtest, "Strategy", ScriptedStrategy)
    return backtest.run_backtest(bars, StrategyConfig(), kwargs.pop("risk", RISK),
                                 kwargs.pop("execution", EXECUTION), AllHours(), **kwargs)


def test_signal_close_cannot_fill_own_bar_and_gap_rechecks_size(monkeypatch):
    result = replay(monkeypatch, [bar(0), bar(1, opening=110, low=100, close=110)],
                    {0: {"entry": True}})
    assert len(result.fills) == 1
    fill = result.fills[0]
    assert (fill.quantity, fill.price, fill.time) == (5, D(110), bar(1).time)
    assert result.orders[0].quantity == 10
    assert result.metrics["ending_cash"] == D(450)
    assert result.metrics["ending_equity"] == D(1000)


def test_samebar_reference_round_trip_never_sends_order(monkeypatch):
    result = replay(monkeypatch, [bar(0), bar(1)],
                    {0: {"entry": True, "signal_exit": True, "held": False},
                     1: {"held": False}})
    assert result.orders == result.fills == []
    assert result.metrics["reference_round_trips_unexecutable"] == 1


def test_entry_confirmed_then_immediately_protected_and_cash_conserved(monkeypatch):
    result = replay(monkeypatch, [bar(0), bar(1, low=89)], {0: {"entry": True}})
    assert [(f.side, f.quantity, f.price) for f in result.fills] == [
        ("buy", 10, D(100)), ("sell", 10, D(90))]
    assert result.positions == []
    assert result.metrics["ending_cash"] == D(900)
    assert result.metrics["entry_bar_stop_ambiguities"] == 1
    assert result.fills[0].time_precision == "bar_open"
    assert result.fills[0].bar_end is None
    assert result.fills[1].time_precision == "intrabar_unknown"
    assert result.fills[1].bar_end == bar(1).time_close
    assert sum((-1 if f.side == "buy" else 1) * f.quantity * f.price - f.fee
               for f in result.fills) + RISK.starting_cash == D(900)


def test_source_stop_is_frozen_at_entry_not_reanchored_to_gap_fill(monkeypatch):
    result = replay(monkeypatch, [bar(0), bar(1, opening=110, low=89, close=100)],
                    {0: {"entry": True}})
    assert [f.price for f in result.fills] == [D(110), D(90)]
    assert result.metrics["ending_cash"] == D(900)


def test_opening_at_stop_rejects_entry(monkeypatch):
    result = replay(monkeypatch, [bar(0), bar(1, opening=90, low=89, close=91)],
                    {0: {"entry": True}})
    assert not result.fills
    assert result.rejections[-1].reason == "opening_at_or_below_stop"


def test_strict_stop_and_decreasing_source_band(monkeypatch):
    result = replay(monkeypatch, [bar(0), bar(1, low=90), bar(2, low=85), bar(3, low=79)],
                    {0: {"entry": True}, 1: {"active_stop": 80},
                     2: {"active_stop": 80}, 3: {"held": False}})
    assert [f.price for f in result.fills] == [D(100), D(80)]
    assert result.fills[-1].time == bar(3).time


def test_adverse_gap_and_fees_are_charged_once_per_order(monkeypatch):
    execution = replace(EXECUTION, fee_per_order=D(1))
    result = replay(monkeypatch, [bar(0), bar(1), bar(2, opening=80, low=79, close=80)],
                    {0: {"entry": True}, 2: {"held": False}}, execution=execution)
    assert [(f.quantity, f.price, f.fee) for f in result.fills] == [
        (9, D(100), D(1)), (9, D(80), D(1))]
    assert result.metrics["ending_cash"] == D(818)
    assert result.metrics["fees"] == D(2)
    assert result.fills[-1].time_precision == "bar_open"
    assert result.fills[-1].bar_end is None


def test_volume_partial_entry_stop_cancels_remainder_and_exits_later(monkeypatch):
    result = replay(monkeypatch, [bar(0), bar(1, low=89, volume=3),
                                 bar(2, opening=85, low=84, close=85, volume=3)],
                    {0: {"entry": True}, 2: {"held": False}})
    assert [(f.side, f.quantity, f.price) for f in result.fills] == [
        ("buy", 3, D(100)), ("sell", 3, D(85))]
    assert result.cancellations[0].remaining_quantity == 7
    assert result.metrics["ending_cash"] == D(955)


def test_signal_exit_only_executes_at_next_open(monkeypatch):
    result = replay(monkeypatch, [bar(0), bar(1), bar(2, opening=105, close=105)],
                    {0: {"entry": True}, 1: {"held": False}, 2: {"held": False}})
    assert [f.time for f in result.fills] == [bar(1).time, bar(2).time]
    assert result.metrics["ending_cash"] == D(1050)


def test_deadline_timer_exits_first_eligible_open_and_reports_lateness(monkeypatch):
    deadline = bar(2).time
    result = replay(monkeypatch, [bar(0), bar(1), bar(3)],
                    {0: {"entry": True, "deadline": deadline}})
    assert result.fills[-1].reason == "scheduled_deadline"
    assert result.deadline_exits[0].lateness_seconds == 300


def test_simultaneous_symbols_reserve_cash_and_slots_deterministically(monkeypatch):
    risk = replace(RISK, max_positions=1)
    bars = [bar(i, symbol=s) for i in (0, 1) for s in ("B", "A")]
    changes = {0: {"entry": True}}
    first = replay(monkeypatch, bars, changes, risk=risk)
    second = replay(monkeypatch, list(reversed(bars)), changes, risk=risk)
    assert first.fills == second.fills
    assert {f.symbol for f in first.fills} == {"A"}
    assert first.metrics["ending_cash"] == D(0)
    assert any(r.reason == "max_positions" for r in first.rejections)


def test_pending_entries_reserve_cash_even_with_multiple_available_slots(monkeypatch):
    result = replay(monkeypatch,
                    [bar(i, symbol=s) for i in (0, 1) for s in ("A", "B")],
                    {0: {"entry": True}})
    assert {f.symbol for f in result.fills} == {"A"}
    assert any(r.reason == "insufficient_budget" for r in result.rejections)


def test_pending_fill_uses_latest_confirmed_active_stop(monkeypatch):
    result = replay(monkeypatch, [bar(0), bar(1, volume=0), bar(2, low=94)],
                    {0: {"entry": True}, 1: {"active_stop": 95}})
    assert [f.price for f in result.fills] == [D(100), D(95)]


@pytest.mark.parametrize("slippage, fixed_fee", [(D(0), D(0)), (D(5), D(1))])
def test_partial_fill_risk_includes_existing_costs_and_new_fill_equity(
        monkeypatch, slippage, fixed_fee):
    risk = replace(RISK, risk_fraction=D(".125"))
    execution = replace(EXECUTION, fee_per_share=D(1), fee_per_order=fixed_fee,
                        slippage_bps=slippage)
    result = replay(monkeypatch, [bar(0), bar(1, volume=5),
                                 bar(2, opening=105, low=104, close=105)],
                    {0: {"entry": True}}, risk=risk, execution=execution)
    assert [f.quantity for f in result.fills] == [5, 3]
    quantity = sum(f.quantity for f in result.fills)
    exit_price = D(90) * (1 - slippage / 10000)
    modeled_loss = (sum(f.quantity * (f.price - exit_price) + f.fee for f in result.fills)
                    + quantity * execution.fee_per_share + fixed_fee)
    assert modeled_loss <= result.equity[-1].equity * risk.risk_fraction


def test_daily_loss_blocks_recovered_equity_until_next_date(monkeypatch):
    risk = replace(RISK, daily_loss_fraction=D(".02"))
    result = replay(monkeypatch, [bar(0), bar(1, low=89), bar(2), bar(3)],
                    {0: {"entry": True}, 2: {"entry": True}}, risk=risk)
    assert len(result.fills) == 2
    assert result.risk_decisions[-1].reason == "daily_loss_latched"


def test_warmup_waits_for_new_entry_and_trade_end_preserves_position(monkeypatch):
    result = replay(monkeypatch, [bar(i) for i in range(5)],
                    {0: {"entry": True}, 2: {"entry": True}},
                    trade_start=bar(1).time + timedelta(seconds=1), trade_end=bar(4).time)
    assert len(result.fills) == 1
    assert result.fills[0].time == bar(3).time
    assert result.positions[0].quantity == 10


def test_future_bars_do_not_change_earlier_fills(monkeypatch):
    changes = {0: {"entry": True}, 2: {"held": False}}
    prefix = replay(monkeypatch, [bar(0), bar(1)], changes)
    extended = replay(monkeypatch, [bar(0), bar(1), bar(2, low=1, close=1)], changes)
    assert extended.fills[:len(prefix.fills)] == prefix.fills


def test_broker_cancellation_prevents_fills_and_enforces_no_shorting():
    broker = OfflineBroker(Portfolio(D(1000)), EXECUTION)
    intent = OrderIntent("one", "A", "buy", 2, START, START, "test", D(90))
    broker.submit_order(intent)
    broker.cancel_order("one")
    with pytest.raises(ValueError, match="status"):
        broker.fill("one", 1, D(100), START)
    broker.submit_order(replace(intent, id="two", side="sell"))
    with pytest.raises(ValueError, match="short"):
        broker.fill("two", 1, D(100), START)


@pytest.mark.parametrize("invalid", [
    replace(bar(0), close=float("nan")),
    replace(bar(0), volume=-1),
    replace(bar(0), low=102),
    replace(bar(0), time_close=bar(0).time_close + timedelta(minutes=1)),
    replace(bar(0), time=bar(0).time + timedelta(seconds=1),
            time_close=bar(0).time_close + timedelta(seconds=1)),
])
def test_direct_api_rejects_invalid_bars(monkeypatch, invalid):
    with pytest.raises(ValueError):
        replay(monkeypatch, [invalid], {})
