from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from turtle_trader.backtest import run_backtest
from turtle_trader.data import ExchangeCalendar
from turtle_trader.domain import Bar, ExecutionConfig, RiskConfig, StrategyConfig
from turtle_trader.research import strategy_warnings

START = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
CONFIG = StrategyConfig(
    entry_length=2, exit_length=2, atr_length=2, atr_multiple=2,
    day_trading=False, exit_strategy="chandelier", chandelier_timeframe="intraday",
    chandelier_length=2, chandelier_atr_length=2, chandelier_multiple=1)
RISK = RiskConfig(starting_cash=D(10000), risk_fraction=D(".01"),
                  allocation_fraction=D(1), daily_loss_fraction=D(".9"))
EXECUTION = ExecutionConfig(slippage_bps=D(0))


def bar(index, opening, high, low, close, volume=1000):
    at = START + timedelta(minutes=5 * index)
    return Bar("A", at, at + timedelta(minutes=5), opening, high, low, close, volume)


def warmup():
    partial = datetime(2024, 12, 27, 20, 55, tzinfo=UTC)
    first = datetime(2024, 12, 30, 20, 55, tzinfo=UTC)
    second = datetime(2024, 12, 31, 20, 55, tzinfo=UTC)
    return [replace(bar(0, 100, 101, 99, 100), time=partial,
                    time_close=partial + timedelta(minutes=5)),
            replace(bar(0, 100, 101, 99, 100), time=first,
                    time_close=first + timedelta(minutes=5)),
            replace(bar(1, 100, 102, 99, 101), time=second,
                    time_close=second + timedelta(minutes=5)),
            bar(2, 102, 106, 101, 105)]


def run(bars, config=CONFIG, execution=EXECUTION):
    calendar = ExchangeCalendar(bars[0].time.date(), bars[-1].time.date())
    return run_backtest(bars, config, RISK, execution, calendar)


def test_chandelier_results_preserve_settings_and_separate_research_labels():
    result = run(warmup())
    assert result.exit_strategy == "chandelier"
    assert result.strategy_settings == asdict(CONFIG)
    assert result.strategy_warnings == strategy_warnings("chandelier")
    assert result.price_basis == "action_free"
    assert result.research_warnings == []
    assert result.events[-1].entry
    assert result.events[-1].initial_stop == pytest.approx(97)
    assert result.events[-1].chandelier_level == pytest.approx(102.25)


def test_confirmed_chandelier_protection_exits_at_adverse_next_open_with_slippage():
    bars = warmup() + [bar(3, 104, 110, 103, 109), bar(4, 100, 101, 99, 100)]
    result = run(bars, execution=replace(EXECUTION, slippage_bps=D(5)))
    assert [(fill.side, fill.time, fill.price) for fill in result.fills] == [
        ("buy", bars[-2].time, D("104.052")),
        ("sell", bars[-1].time, D("99.9500"))]
    assert result.orders[0].stop == D("97")
    assert result.fills[-1].reason == "protective_stop"
    assert result.events[-2].proposed_stop == pytest.approx(104.625)


def test_new_stop_never_retroactively_executes_against_same_candle_low():
    bars = warmup() + [bar(3, 104, 110, 103, 109)]
    result = run(bars)
    event = result.events[-1]
    assert event.stop_used == pytest.approx(102.25)
    assert event.proposed_stop == pytest.approx(104.625)
    assert event.stop_used < bars[-1].low < event.proposed_stop
    assert event.held
    assert [fill.side for fill in result.fills] == ["buy"]
    assert result.positions[0].stop == D("104.625")


def test_pending_entry_rejects_open_below_latest_published_chandelier_stop():
    bars = warmup() + [bar(3, 104, 110, 103, 109, volume=0),
                       bar(4, 104, 105, 103, 104)]
    result = run(bars)
    assert result.events[-2].held
    assert result.events[-2].proposed_stop == pytest.approx(104.625)
    assert result.fills == []
    assert result.rejections[-1].reason == "opening_at_or_below_stop"
    assert result.cancellations[-1].remaining_quantity == result.orders[0].quantity


def test_close_below_new_stop_queues_next_open_exit_without_retroactive_stop_fill():
    bars = warmup() + [bar(3, 104, 120, 103, 104), bar(4, 100, 101, 99, 100)]
    result = run(bars, execution=replace(EXECUTION, slippage_bps=D(5)))
    event = result.events[-2]
    assert event.stop_used == pytest.approx(102.25)
    assert event.proposed_stop == pytest.approx(109.625)
    assert event.exit_reason == "new_stop_close_breach"
    assert event.signal_exit_price == 104
    assert [fill.time for fill in result.fills] == [bars[-2].time, bars[-1].time]
    assert result.fills[-1].price == D("99.9500")
    assert result.fills[-1].reason == "new_stop_close_breach"


def test_partial_entry_close_breach_cancels_remainder_and_sells_confirmed_shares():
    bars = warmup() + [bar(3, 104, 120, 103, 104, volume=3),
                       bar(4, 100, 101, 99, 100)]
    result = run(bars)
    assert [fill.quantity for fill in result.fills] == [3, 3]
    assert [fill.side for fill in result.fills] == ["buy", "sell"]
    assert result.fills[-1].reason == "new_stop_close_breach"
    assert result.cancellations[0].reason == "new_stop_close_breach"
    assert result.cancellations[0].remaining_quantity == result.orders[0].quantity - 3
    assert result.positions == []
    assert all(order.status != "open" for order in result.order_states)


def test_breakout_and_new_stop_close_breach_coalesce_without_actual_buy():
    bars = warmup()[:-1] + [bar(2, 102, 120, 101, 103), bar(3, 101, 102, 100, 101)]
    result = run(bars)
    event = result.events[-2]
    assert event.entry
    assert not event.held
    assert event.exit_reason == "new_stop_close_breach"
    assert result.orders == result.fills == []
    assert result.metrics["reference_round_trips_unexecutable"] == 1


def test_default_revelio_result_retains_no_chandelier_warning():
    config = replace(CONFIG, exit_strategy="revelio")
    result = run(warmup(), config)
    assert result.exit_strategy == "revelio"
    assert result.strategy_warnings == []
    assert result.strategy_settings == asdict(config)


def test_actual_ratchet_selection_changes_published_protection_without_reanchoring_risk():
    bars = warmup() + [bar(3, 104, 150, 103, 140), bar(4, 140, 141, 127, 140),
                       bar(5, 140, 140, 133, 139)]
    ratcheted = run(bars)
    raw = run(bars, replace(CONFIG, chandelier_ratchet=False))
    assert ratcheted.events[-1].chandelier_level == pytest.approx(127.65625)
    assert ratcheted.events[-1].chandelier_stop == pytest.approx(130.3125)
    assert raw.events[-1].chandelier_stop == pytest.approx(127.65625)
    assert ratcheted.positions[0].stop == D("130.3125")
    assert raw.positions[0].stop == D("127.65625")
    assert ratcheted.orders[0].stop == raw.orders[0].stop == D(97)


def test_session_line_publishes_only_after_first_next_session_confirmed_candle():
    next_session = datetime(2025, 1, 3, 14, 30, tzinfo=UTC)
    first = replace(bar(4, 109, 111, 100, 110), time=next_session,
                    time_close=next_session + timedelta(minutes=5))
    following = replace(bar(5, 104, 105, 103, 104),
                        time=next_session + timedelta(minutes=5),
                        time_close=next_session + timedelta(minutes=10))
    bars = warmup() + [bar(3, 104, 110, 103, 109), first, following]
    result = run(bars, replace(CONFIG, chandelier_timeframe="session"))
    assert result.events[-3].chandelier_level == pytest.approx(99.5)
    assert result.events[-2].stop_used == pytest.approx(99.5)
    assert result.events[-2].proposed_stop == pytest.approx(104.25)
    assert result.events[-2].held
    assert [fill.time for fill in result.fills] == [bars[-3].time, following.time]
    assert result.fills[-1].price == D(104)


@pytest.mark.parametrize("day_trading", [False, True])
def test_revelio_daily_timer_only_applies_when_schedule_enabled(day_trading):
    next_session = datetime(2025, 1, 3, 14, 30, tzinfo=UTC)
    next_bar = replace(bar(4, 109, 111, 100, 110), time=next_session,
                       time_close=next_session + timedelta(minutes=5))
    bars = warmup() + [bar(3, 104, 110, 103, 109), next_bar]
    result = run(bars, replace(CONFIG, exit_strategy="revelio", day_trading=day_trading))
    assert result.fills[0].side == "buy"
    if day_trading:
        assert len(result.fills) == 2
        assert result.fills[-1].reason == "scheduled_deadline"
        assert result.fills[-1].time == next_session
    else:
        assert len(result.fills) == 1
        assert result.positions and result.positions[0].quantity > 0
        assert not result.deadline_exits
