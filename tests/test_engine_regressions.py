"""Independent regression scenarios for causal engine and accounting behavior."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from turtle_trader.backtest import _Replay, _sessions
from turtle_trader.domain import Bar, ExecutionConfig, RiskConfig, StrategyConfig
from turtle_trader.risk import SizingCapital

D = Decimal
START = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
FREE = ExecutionConfig(slippage_bps=D(0))


def bar(index: int = 0, *, opening: float = 100, high: float = 101,
        low: float = 99, close: float = 100, volume: int = 100000,
        symbol: str = "ABC") -> Bar:
    at = START + timedelta(minutes=5 * index)
    return Bar(symbol, at, at + timedelta(minutes=5), opening, high, low, close, volume)


def engine(*, strategy: StrategyConfig | None = None,
           risk: RiskConfig | None = None, execution: ExecutionConfig = FREE,
           symbols: set[str] | None = None) -> _Replay:
    replay = _Replay(strategy or StrategyConfig(),
                     risk or RiskConfig(unit_fraction=D("0.001")),
                     execution, symbols or {"ABC"}, None, None)
    for symbol, rule in replay.strategies.items():
        for _ in range(60):
            rule.update_session(101, 99, 100)
        rule.publish_week()
        replay.weekly_shares[symbol] = 12
        replay.weeks[symbol] = START.isocalendar()[:2]
    replay.sizing.observe(START, replay.portfolio.equity)
    return replay


@pytest.mark.parametrize("exit_strategy", ["turtle_channel", "chandelier"])
def test_short_shadow_win_filters_actual_longs_even_when_shorts_disabled(
    exit_strategy: str,
) -> None:
    replay = engine(strategy=StrategyConfig(entry_strategy="turtle_s1",
                                             exit_strategy=exit_strategy, allow_short=False))
    rule = replay.strategies["ABC"]
    replay.path(bar(opening=98.99, high=98.99, low=98.99, close=98.99), True)
    assert rule.shadow_direction == -1
    assert not replay.result.fills
    for _ in range(20):
        rule.update_session(97, 95, 96)
    replay.path(bar(1, opening=97.01, high=97.01, low=97.01, close=97.01), True)
    assert rule.last_winner
    assert rule.shadow_direction == 0
    assert rule.levels().entry_long == 101.01
    assert rule.shadow_triggers()[0] == 97.01
    replay.path(bar(2, opening=98, high=98, low=98, close=98), True)
    assert not replay.result.fills


def test_batch_commits_lower_equity_path_without_candidate_state_leakage() -> None:
    replay = engine()
    candle = bar(opening=100, high=103, low=98, close=102)
    high_first, low_first = replay.fork(), replay.fork()
    high_first.path(candle, True)
    low_first.path(candle, False)
    assert high_first.portfolio.equity < low_first.portfolio.equity
    expected = high_first.portfolio.equity
    key = ("ABC", START.date())
    replay.batch([candle], {key: candle.time_close}, {key: (103, 98, 102)})
    assert replay.portfolio.equity == expected
    assert replay.result.fills == high_first.result.fills
    assert len(replay.result.execution_ambiguities) == 1
    assert not replay.portfolio.lots


def test_addition_spacing_uses_actual_slipped_fill_not_trigger_price() -> None:
    replay = engine(risk=RiskConfig(unit_fraction=D("0.001"), max_units=2),
                    execution=ExecutionConfig(slippage_bps=D(10)))
    replay.path(bar(opening=101.01, high=104, low=101.01, close=104), True)
    fills = [fill for fill in replay.result.fills if fill.intent == "open"]
    assert len(fills) == 2
    assert fills[0].price == D("101.01") * D("1.001")
    assert fills[1].price == (fills[0].price + D(1)) * D("1.001")
    assert fills[1].price != D("102.01") * D("1.001")


def test_pending_exit_continues_after_price_recovers_above_trigger() -> None:
    replay = engine()
    replay.path(bar(opening=101.01, high=101.01, low=101.01, close=101.01), True)
    replay.path(bar(1, opening=95, high=95, low=95, close=95, volume=3), True)
    assert replay.pending("ABC", "reduce")[0].remaining == 9
    replay.path(bar(2, opening=100, high=100, low=100, close=100, volume=9), True)
    assert not replay.portfolio.lots
    assert not replay.pending("ABC", "reduce")
    assert replay.result.fills[-1].price == 100
    assert replay.result.fills[-1].quantity == 9
    assert len([fill for fill in replay.result.fills if fill.intent == "open"]) == 1


def test_annual_sizing_reset_uses_opening_marks_before_new_fills() -> None:
    replay = engine()
    replay.path(bar(opening=101.01, high=101.01, low=101.01, close=101.01), True)
    future = replace(bar(opening=102, high=102, low=102, close=102),
                     time=datetime(2027, 1, 4, 14, 30, tzinfo=UTC),
                     time_close=datetime(2027, 1, 4, 14, 35, tzinfo=UTC))
    previous_fills = len(replay.result.fills)
    expected = D(25000) + 12 * (D(102) - D("101.01"))
    replay.before_batch([future])
    assert replay.sizing.baseline == expected
    assert replay.sizing.capital == expected
    assert replay.sizing.year == 2027
    assert len(replay.result.fills) == previous_fills


def test_partial_boundary_sessions_are_excluded_without_polluting_daily_history() -> None:
    class Calendar:
        def eligible(self, at: datetime, market_hours: str = "regular") -> bool:
            return True

        def bounds(self, day: date, market_hours: str) -> tuple[datetime, datetime]:
            opening = datetime.combine(day, START.timetz())
            return opening, opening + timedelta(minutes=10)

    first = bar()
    middle = replace(bar(), time=START + timedelta(days=1),
                     time_close=START + timedelta(days=1, minutes=5))
    middle_end = replace(middle, time=middle.time_close,
                         time_close=middle.time_close + timedelta(minutes=5))
    last = replace(bar(), time=START + timedelta(days=2),
                   time_close=START + timedelta(days=2, minutes=5))
    accepted, closes, daily = _sessions([first, middle, middle_end, last], Calendar(), 5)
    assert accepted == [middle, middle_end]
    assert set(closes) == set(daily) == {("ABC", middle.time.date())}


def test_new_intraday_chandelier_level_cannot_exit_the_candle_that_publishes_it() -> None:
    config = StrategyConfig(exit_strategy="chandelier", atr_multiple=10.0,
                            chandelier_timeframe="intraday", chandelier_length=2,
                            chandelier_atr_length=2, chandelier_multiple=1.0)
    replay = engine(strategy=config, risk=RiskConfig(unit_fraction=D("0.001"), max_units=1))
    rule = replay.strategies["ABC"]
    rule.update_bar(bar(opening=95, high=100, low=90, close=95))
    rule.update_bar(bar(opening=95, high=100, low=90, close=95))
    candle = bar(opening=101.01, high=120, low=100, close=115)
    following = bar(1, opening=102.5, high=102.5, low=102.5, close=102.5)
    key = ("ABC", START.date())
    replay.batch([candle], {key: following.time_close}, {key: (120, 100, 102.5)})
    assert len(replay.portfolio.lots) == 1
    assert all(fill.intent == "open" for fill in replay.result.fills)
    assert rule.levels().exit_long is None  # forked state must replace the original object
    assert replay.strategies["ABC"].levels().exit_long == 102.5
    replay.batch([following], {key: following.time_close}, {key: (120, 100, 102.5)})
    assert not replay.portfolio.lots
    assert replay.result.fills[-1].reason == "strategy_exit"
    assert replay.result.fills[-1].price == D("102.5")


def test_other_symbol_cannot_spend_cash_reserved_for_partial_unit() -> None:
    replay = engine(risk=RiskConfig(starting_cash=D(1500), unit_fraction=D("0.02")),
                    symbols={"ABC", "XYZ"})
    replay.path(bar(opening=101.01, high=101.01, low=101.01, close=101.01, volume=3), True)
    assert replay.reserved_cash() == 9 * D("101.01")
    replay.path(bar(symbol="XYZ", opening=101.01, high=101.01, low=101.01, close=101.01), True)
    assert "XYZ" not in replay.campaigns
    assert replay.result.rejections[-1].reason == "insufficient_cash"


def test_short_partial_unit_reserves_extra_collateral_without_reusing_proceeds() -> None:
    execution = ExecutionConfig(slippage_bps=D(0), shortable_symbols=("ABC",),
                                borrow_rate=D("0.365"), fee_per_share=D("0.1"),
                                fee_per_order=D(2))
    replay = engine(strategy=StrategyConfig(allow_short=True), execution=execution)
    replay.path(bar(opening=98.99, high=98.99, low=98.99, close=98.99, volume=3), True)
    assert replay.portfolio.fees == D("2.3")
    assert replay.portfolio.collateral == 3 * D("98.99") * D("1.5")
    assert replay.reserved_cash() == 9 * (D("98.99") * D("0.5") + D("0.1"))
    assert replay.portfolio.available_cash < D(25000)
    replay.borrow_day = START.date() - timedelta(days=3)
    replay.before_batch([bar(1, opening=98.99, high=98.99, low=98.99, close=98.99)])
    assert replay.portfolio.borrow_costs == 3 * D("98.99") * D("0.365") * 3 / 365


def test_mirrored_long_and_short_gap_stops_have_equal_losses_and_fees() -> None:
    execution = ExecutionConfig(slippage_bps=D(0), fee_per_share=D("0.1"),
                                fee_per_order=D(2), shortable_symbols=("ABC",),
                                borrow_rate=D(0))
    long = engine(execution=execution)
    short = engine(strategy=StrategyConfig(allow_short=True), execution=execution)
    long.path(bar(opening=101.01, high=101.01, low=101.01, close=101.01), True)
    short.path(bar(opening=98.99, high=98.99, low=98.99, close=98.99), False)
    long.path(bar(1, opening=95, high=95, low=95, close=95), True)
    short.path(bar(1, opening=105, high=105, low=105, close=105), False)
    assert not long.portfolio.lots and not short.portfolio.lots
    assert long.portfolio.realized_pnl == short.portfolio.realized_pnl == -12 * D("6.01")
    assert long.portfolio.fees == short.portfolio.fees == D("6.4")
    assert long.portfolio.equity == short.portfolio.equity
    assert short.portfolio.collateral == 0


def test_cancelled_partial_addition_blocks_further_additions_after_cash_recovers() -> None:
    replay = engine(risk=RiskConfig(starting_cash=D(3000), unit_fraction=D("0.01")))
    replay.path(bar(opening=101.01, high=101.01, low=101.01, close=101.01), True)
    replay.path(bar(1, opening=102.01, high=102.01, low=102.01, close=102.01, volume=3), True)
    assert replay.campaigns["ABC"].units == 2
    assert replay.pending("ABC", "open")[0].remaining == 9
    replay.path(bar(2, opening=200, high=200, low=200, close=200), True)
    assert replay.campaigns["ABC"].blocked_additions
    assert not replay.pending("ABC", "open")
    assert replay.result.cancellations[-1].reason == "insufficient_cash"
    orders_before = len(replay.result.orders)
    assert replay.portfolio.available_cash > 12 * D(103)
    replay.path(bar(3, opening=103, high=103, low=103, close=103), True)
    assert len(replay.result.orders) == orders_before
    assert [lot.quantity for lot in replay.portfolio.lots.values()] == [12, 3]


def test_quiet_candle_skips_cloning_with_identical_observable_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    optimized, simulated = engine(), engine()
    candle = bar(opening=100, high=100.5, low=99.5, close=100.2)
    assert not optimized.can_transition(candle)

    def unexpected_clone() -> _Replay:
        raise AssertionError("quiet candle cloned execution state")

    monkeypatch.setattr(optimized, "fork", unexpected_clone)
    monkeypatch.setattr(simulated, "can_transition", lambda _: True)
    key = ("ABC", START.date())
    for replay in (optimized, simulated):
        replay.batch([candle], {key: candle.time_close}, {key: (100.5, 99.5, 100.2)})
    assert optimized.finish() == simulated.finish()
    assert optimized.strategies["ABC"].levels() == simulated.strategies["ABC"].levels()


@pytest.mark.parametrize("reverse_input", [False, True])
def test_synchronized_reduction_of_later_symbol_funds_same_phase_earlier_entry(
    reverse_input: bool,
) -> None:
    execution = ExecutionConfig(slippage_bps=D(0), shortable_symbols=("ZZZ",), borrow_rate=D(0))
    replay = engine(strategy=StrategyConfig(allow_short=True), execution=execution,
                    risk=RiskConfig(starting_cash=D(1500), unit_fraction=D("0.02")),
                    symbols={"AAA", "ZZZ"})
    replay.path(bar(symbol="ZZZ", opening=98.99, high=98.99, low=98.99, close=98.99), True)
    assert replay.portfolio.available_cash < 12 * D("101.01")
    candle_a = bar(1, symbol="AAA", opening=100, high=102.02, low=100, close=102.02)
    candle_z = bar(1, symbol="ZZZ", opening=100, high=102.02, low=100, close=102.02)
    bars = [candle_z, candle_a] if reverse_input else [candle_a, candle_z]
    previous_fills = len(replay.result.fills)
    closes = {(b.symbol, START.date()): b.time_close for b in bars}
    daily = {(b.symbol, START.date()): (b.high, b.low, b.close) for b in bars}
    replay.batch(bars, closes, daily)
    fills = replay.result.fills[previous_fills:]
    assert [(fill.symbol, fill.intent) for fill in fills] == [
        ("ZZZ", "reduce"), ("AAA", "open"),
    ]
    assert fills[0].price == D("101.01")
    assert fills[1].price == D("101.01")
    assert set(replay.campaigns) == {"AAA"}


def test_severe_drawdown_immediately_exhausts_sizing_capital() -> None:
    capital = SizingCapital(D(1000000))
    capital.observe(START, D(1000000))
    capital.observe(START + timedelta(days=1), D(400000))
    assert capital.capital == 0
    assert capital.anchor == D(400000)


def test_unit_limit_counts_current_lots_instead_of_lifetime_unit_identifiers() -> None:
    replay = engine()
    for index, price in enumerate((101.01, 105, 109, 113)):
        replay.path(bar(index, opening=price, high=price, low=price, close=price), True)
    campaign = replay.campaigns["ABC"]
    assert campaign.units == 4
    assert [lot.stop for lot in replay.portfolio.lots.values()] == [
        D("100.01"), D(103), D(106), D(109),
    ]
    replay.path(bar(4, opening=108, high=108, low=108, close=108), True)
    assert len(replay.portfolio.lots) == 3
    assert not replay.pending("ABC", "reduce")
    assert campaign.last_fill == D(113)
    replay.path(bar(5, opening=114, high=114, low=114, close=114), True)
    assert len(replay.portfolio.lots) == 4
    assert campaign.units == 5
    fills = [fill for fill in replay.result.fills if fill.intent == "open"]
    assert len(fills) == 5
    assert fills[-1].unit_id.endswith("unit-5")
    assert fills[-1].price == D(114)
