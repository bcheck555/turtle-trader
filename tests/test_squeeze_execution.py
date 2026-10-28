"""Causal signals and independently priced bracket execution regressions."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from turtle_trader.backtest import _Replay, run_backtest
from turtle_trader.config import load_config
from turtle_trader.domain import Bar, ExecutionConfig, RiskConfig, StrategyConfig
from turtle_trader.squeeze import SqueezeSignal
from turtle_trader.strategy import SqueezeEntry

D = Decimal
START = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)


def bar(i: int = 0, opening: float = 100, high: float | None = None,
        low: float | None = None, close: float | None = None, volume: int = 10000,
        symbol: str = "QQQ") -> Bar:
    at = START + timedelta(minutes=i * 30)
    return Bar(symbol, at, at + timedelta(minutes=30), opening,
               high if high is not None else opening, low if low is not None else opening,
               close if close is not None else opening, volume)


def replay(*, short: bool = False, flip: bool = True, slippage: str = "0",
           symbols: set[str] | None = None) -> _Replay:
    config = StrategyConfig(entry_strategy="squeeze_momentum", exit_strategy="atr_bracket",
                            interval_minutes=30, allow_short=short, squeeze_flip_on_opposite=flip,
                            squeeze_use_adx=False, squeeze_use_wma=False)
    execution = ExecutionConfig(slippage_bps=D(slippage), shortable_symbols=("QQQ", "ZZZ"),
                                borrow_rate=D(0))
    return _Replay(config, RiskConfig(starting_cash=D(10000), unit_fraction=D("0.015"),
                                      max_units=1), execution, symbols or {"QQQ"}, None, None)


def signal(engine: _Replay, direction: int = 1, at: datetime = START,
           n: float = 2, symbol: str = "QQQ") -> None:
    rule = engine.strategies[symbol].entry_rule
    assert isinstance(rule, SqueezeEntry)
    rule.indicator.atr = n
    rule.signal = SqueezeSignal(direction, at, n)


def execute(engine: _Replay, *bars: Bar) -> None:
    closes = {(b.symbol, b.time.date()): b.time_close for b in bars}
    daily = {(b.symbol, b.time.date()): (b.high, b.low, b.close) for b in bars}
    engine.batch(list(bars), closes, daily)


def test_release_cannot_fill_its_own_bar_and_freezes_signal_atr() -> None:
    engine = replay()
    config = replace(engine.strategy_config, squeeze_bb_length=3, squeeze_kc_length=3,
                     squeeze_momentum_length=3, squeeze_atr_length=2)
    engine = _Replay(config, engine.risk_config, engine.config, {"QQQ"}, None, None)
    for i in range(6):
        execute(engine, bar(i, high=101, low=99))
    release = bar(6, 110, high=111, low=109)
    execute(engine, release)
    assert not engine.result.fills
    published = engine.strategies["QQQ"].close_signal
    assert published is not None and published.atr == 6.5
    following = bar(7, 112)
    execute(engine, following)
    fill = engine.result.fills[0]
    assert fill.time == published.known_at == following.time
    assert fill.price == 112
    assert engine.portfolio.lots[fill.unit_id].stop == 99
    assert engine.strategies["QQQ"].target == 131.5


def test_both_bracket_levels_choose_lower_equity_path() -> None:
    engine = replay()
    signal(engine)
    execute(engine, bar(high=108, low=94))
    assert [(f.intent, f.price) for f in engine.result.fills] == [
        ("open", D(100)), ("reduce", D(96)),
    ]
    assert engine.portfolio.equity == 9700
    assert engine.result.execution_ambiguities
    assert not engine.portfolio.lots


@pytest.mark.parametrize("opening,reason", [(90, "protective_stop"), (110, "take_profit")])
def test_bracket_gap_fills_at_observed_open(opening: float, reason: str) -> None:
    engine = replay()
    signal(engine)
    execute(engine, bar())
    execute(engine, bar(1, opening))
    assert engine.result.fills[-1].price == opening
    assert engine.result.fills[-1].reason == reason
    assert engine.result.fills[-1].time_precision == "bar_open"


def test_partial_entry_expires_and_brackets_remain_anchored() -> None:
    engine = replay()
    signal(engine)
    execute(engine, bar(volume=5))
    assert engine.result.fills[0].quantity == 5
    assert not engine.pending("QQQ", "open")
    assert engine.result.cancellations[-1].reason == "entry_bar_expired"
    execute(engine, bar(1, 101))
    assert len(engine.result.fills) == 1
    assert next(iter(engine.portfolio.lots.values())).stop == 96
    assert engine.strategies["QQQ"].target == 106


def test_partially_filled_exit_remains_active_after_price_recovers() -> None:
    engine = replay()
    signal(engine)
    execute(engine, bar())
    execute(engine, bar(1, 95, volume=2))
    assert engine.pending("QQQ", "reduce")[0].remaining == 73
    execute(engine, bar(2, 100))
    assert not engine.portfolio.lots
    assert engine.result.fills[-1].quantity == 73
    assert engine.result.fills[-1].price == 100


@pytest.mark.parametrize("allow_short", [False, True])
def test_opposite_signal_closes_before_optional_reverse(allow_short: bool) -> None:
    engine = replay(short=allow_short)
    signal(engine)
    execute(engine, bar())
    signal(engine, -1, at=bar(1).time)
    before = len(engine.result.fills)
    execute(engine, bar(1, 101))
    fills = engine.result.fills[before:]
    assert fills[0].intent == "reduce" and fills[0].reason == "opposite_signal"
    if allow_short:
        assert [f.intent for f in fills] == ["reduce", "open"]
        assert fills[1].direction == -1
    else:
        assert len(fills) == 1
        assert not engine.portfolio.lots
        assert engine.result.rejections[-1].reason == "short_not_permitted"


def test_same_direction_and_disabled_flip_signals_do_not_add_or_close() -> None:
    engine = replay(flip=False)
    signal(engine)
    execute(engine, bar())
    for i, direction in enumerate((1, -1), 1):
        signal(engine, direction, at=bar(i).time)
        execute(engine, bar(i, 101))
    assert len(engine.result.fills) == 1
    assert len(engine.portfolio.lots) == 1


def test_partial_opposite_exit_cannot_open_short_until_flat_or_reuse_signal() -> None:
    engine = replay(short=True)
    signal(engine)
    execute(engine, bar())
    signal(engine, -1, at=bar(1).time)
    execute(engine, bar(1, 101, volume=2))
    assert engine.pending("QQQ", "reduce")[0].remaining == 73
    assert all(lot.direction == 1 for lot in engine.portfolio.lots.values())
    execute(engine, bar(2, 101))
    assert not engine.portfolio.lots
    assert [fill.intent for fill in engine.result.fills] == ["open", "reduce", "reduce"]


def test_signal_expires_on_zero_volume_bar_without_later_entry() -> None:
    engine = replay()
    signal(engine)
    execute(engine, bar(volume=0))
    execute(engine, bar(1))
    assert not engine.result.fills
    assert not engine.campaigns


def test_cash_cap_includes_slippage_and_fees() -> None:
    engine = replay(slippage="5")
    engine.config = replace(engine.config, fee_per_share=D("0.1"), fee_per_order=D(2))
    engine.broker.config = engine.config
    signal(engine, n=0.1)
    execute(engine, bar())
    fill = engine.result.fills[0]
    assert fill.quantity == 99
    assert fill.price == D("100.0500")
    assert fill.fee == D("11.9")
    assert engine.portfolio.available_cash >= 0
    assert engine.result.risk_decisions[0].planned_risk == D("19.8")


def test_future_signal_cannot_execute_early_or_persist() -> None:
    engine = replay()
    signal(engine, at=START + timedelta(minutes=60))
    execute(engine, bar())
    assert not engine.result.fills
    assert engine.strategies["QQQ"].close_signal is None


def test_warmup_does_not_execute_and_overnight_signal_uses_next_open() -> None:
    engine = replay()
    engine.trade_start = START + timedelta(days=1)
    signal(engine)
    execute(engine, bar())
    assert not engine.result.fills
    next_day = replace(bar(), time=engine.trade_start,
                       time_close=engine.trade_start + timedelta(minutes=30))
    signal(engine, at=START + timedelta(hours=6))
    execute(engine, next_day)
    assert engine.result.fills[0].time == next_day.time


def test_synchronized_exit_of_later_symbol_funds_earlier_market_entry() -> None:
    engine = replay(symbols={"AAA", "ZZZ"})
    signal(engine, n=0.1, symbol="ZZZ")
    execute(engine, bar(symbol="ZZZ"))
    signal(engine, at=bar(1).time, n=0.1, symbol="AAA")
    signal(engine, -1, at=bar(1).time, n=0.1, symbol="ZZZ")
    before = len(engine.result.fills)
    execute(engine, bar(1, symbol="AAA"), bar(1, symbol="ZZZ"))
    assert [(f.symbol, f.intent) for f in engine.result.fills[before:]] == [
        ("ZZZ", "reduce"), ("AAA", "open"),
    ]


def test_squeeze_rejects_pyramiding_from_config_and_direct_api() -> None:
    config = replay().strategy_config
    with pytest.raises(ValueError, match="max_units=1"):
        run_backtest([], config, RiskConfig(), ExecutionConfig())
    with pytest.raises(ValueError, match="max_units=1"):
        load_config(overrides=[
            'strategy.entry_strategy="squeeze_momentum"',
            'strategy.exit_strategy="atr_bracket"',
        ])


def test_close_signal_fill_sequence_is_invariant_to_future_bars() -> None:
    short = replay()
    extended = replay()
    for engine in (short, extended):
        signal(engine)
        execute(engine, bar())
        execute(engine, bar(1, 107))
    prefix = list(extended.result.fills)
    for i in range(2, 6):
        execute(extended, bar(i, 150))
    assert extended.result.fills == prefix == short.result.fills
