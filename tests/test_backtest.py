from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from turtle_trader.backtest import _Replay, run_backtest
from turtle_trader.domain import Bar, ExecutionConfig, RiskConfig, StrategyConfig
from turtle_trader.portfolio import Portfolio

D = Decimal
START = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
FREE = ExecutionConfig(slippage_bps=D(0))


class FixtureCalendar:
    """Explicit synthetic five-minute sessions for focused engine tests."""

    def eligible(self, at: datetime, market_hours: str = "regular") -> bool:
        return True


def candle(index: int = 0, *, opening: float = 100, high: float = 101,
           low: float = 99, close: float = 100, volume: int = 100000,
           symbol: str = "ABC") -> Bar:
    at = START + timedelta(days=index)
    return Bar(symbol, at, at + timedelta(minutes=5), opening, high, low, close, volume)


def warmed(*, strategy: StrategyConfig | None = None,
           risk: RiskConfig | None = None, execution: ExecutionConfig = FREE,
           symbols: set[str] | None = None) -> _Replay:
    replay = _Replay(strategy or StrategyConfig(), risk or RiskConfig(unit_fraction=D("0.001")),
                     execution, symbols or {"ABC"}, None, None)
    for rule in replay.strategies.values():
        for _ in range(60):
            rule.update_session(101, 99, 100)
        rule.publish_week()
    replay.weekly_shares = {s: 12 for s in replay.strategies}
    replay.sizing.observe(START, replay.portfolio.equity)
    return replay


def test_default_system_requires_55_sessions_and_executes_intraday() -> None:
    bars = [candle(i) for i in range(60)]
    bars.append(candle(60, high=102, close=101.5))
    result = run_backtest(bars, StrategyConfig(), RiskConfig(unit_fraction=D("0.001")),
                          FREE, FixtureCalendar())
    opening = [f for f in result.fills if f.intent == "open"]
    assert opening and opening[0].price == D("101.01")
    assert opening[0].time == bars[-1].time
    assert opening[0].time_precision == "intrabar_unknown"
    assert all(f.direction == 1 for f in result.fills)
    assert result.positions


def test_gap_fill_is_open_not_channel_and_records_actual_stop() -> None:
    replay = warmed()
    bar = candle(opening=105, high=105, low=105, close=105)
    replay.path(bar, True)
    fill = replay.result.fills[0]
    assert fill.price == D(105)
    assert fill.time_precision == "bar_open"
    assert replay.portfolio.lots[fill.unit_id].stop == D(101)


def test_multiple_additions_and_separate_lot_stops() -> None:
    replay = warmed()
    replay.path(candle(opening=101.01, high=104.5, low=101.01, close=104), True)
    opens = [f for f in replay.result.fills if f.intent == "open"]
    assert [f.price for f in opens] == [D("101.01"), D("102.01"), D("103.01"), D("104.01")]
    assert len(replay.portfolio.lots) == 4
    assert all(lot.stop == D("100.01") for lot in replay.portfolio.lots.values())


def test_addition_gap_preserves_different_stops() -> None:
    replay = warmed()
    replay.path(candle(opening=101.01, high=101.01, low=101.01, close=101.01), True)
    replay.path(candle(1, opening=105, high=105, low=105, close=105), True)
    stops = [p.stop for p in replay.portfolio.lots.values()]
    assert stops == [D("98.01"), D(101)]


def test_partial_unit_blocks_additions_and_remainder_reserves_cash() -> None:
    replay = warmed()
    replay.path(candle(opening=101.01, high=105, low=101.01, close=105, volume=3), True)
    assert len(replay.result.orders) == 1
    assert replay.result.fills[0].quantity == 3
    assert replay.pending("ABC", "open")[0].remaining == 9
    assert replay.reserved_cash() == D("909.09")
    replay.path(candle(1, opening=105, high=105, low=105, close=105, volume=9), True)
    assert len(replay.result.orders) == 1
    assert not replay.pending("ABC", "open")


def test_stop_cancels_unfilled_entry_remainder() -> None:
    replay = warmed()
    replay.path(candle(opening=101.01, high=101.01, low=101.01, close=101.01, volume=3), True)
    replay.path(candle(1, opening=95, high=95, low=95, close=95, volume=100), True)
    assert not replay.portfolio.lots
    assert not replay.pending("ABC", "open")
    assert replay.result.cancellations[0].remaining_quantity == 9
    assert replay.result.fills[-1].price == 95


def test_whole_unaffordable_unit_rejected_without_shrinking() -> None:
    replay = warmed(risk=RiskConfig(starting_cash=D(1000), unit_fraction=D(".1")))
    replay.weekly_shares["ABC"] = 12
    replay.path(candle(opening=101.01, high=101.01, low=101.01, close=101.01), True)
    assert not replay.result.fills
    assert replay.result.rejections[0].reason == "insufficient_cash"


def test_remainder_rechecked_after_gap_without_silent_resize() -> None:
    replay = warmed(risk=RiskConfig(starting_cash=D(1300)))
    replay.path(candle(opening=101.01, high=101.01, low=101.01, close=101.01, volume=3), True)
    replay.path(candle(1, opening=200, high=200, low=200, close=200), True)
    assert not replay.pending("ABC", "open")
    assert any(r.reason == "insufficient_cash" for r in replay.result.rejections)


def test_path_cloning_does_not_mutate_original_or_share_shadow_state() -> None:
    replay = warmed(strategy=StrategyConfig(entry_strategy="turtle_s1"))
    child = replay.fork()
    child.path(candle(opening=101.01, high=101.01, low=95, close=100), True)
    assert not replay.result.fills and not replay.portfolio.lots
    assert replay.strategies["ABC"].shadow_direction == 0
    assert child.result.fills
    assert {e.kind for e in child.result.events} >= {"shadow_entry", "shadow_exit"}


def test_short_option_requires_explicit_borrow_configuration() -> None:
    with pytest.raises(ValueError, match="shorts require"):
        run_backtest([], StrategyConfig(allow_short=True), RiskConfig(), FREE)


def test_short_fill_stop_collateral_and_cover() -> None:
    execution = ExecutionConfig(slippage_bps=D(0), shortable_symbols=("ABC",), borrow_rate=D(".1"))
    replay = warmed(strategy=StrategyConfig(allow_short=True), execution=execution)
    replay.path(candle(opening=98.99, high=98.99, low=98.99, close=98.99), True)
    lot = next(iter(replay.portfolio.lots.values()))
    assert lot.direction == -1 and lot.stop == D("102.99")
    assert replay.portfolio.collateral == D("1781.820")
    assert replay.portfolio.available_cash == D("24406.060")
    replay.path(candle(1, opening=105, high=105, low=105, close=105), True)
    assert not replay.portfolio.lots
    assert replay.portfolio.collateral == 0
    assert replay.result.fills[-1].side == "buy"
    assert replay.result.fills[-1].intent == "reduce"


def test_borrow_weekend_and_forced_cover_queue() -> None:
    execution = ExecutionConfig(slippage_bps=D(0), shortable_symbols=("ABC",),
                                borrow_rate=D(".365"))
    replay = warmed(strategy=StrategyConfig(allow_short=True), execution=execution)
    replay.path(candle(opening=98.99, high=98.99, low=98.99, close=98.99), True)
    replay.borrow_day = date(2026, 1, 2)
    replay.before_batch([candle(opening=120, high=120, low=120, close=120)])
    assert replay.portfolio.borrow_costs == D("3.56364")
    assert replay.pending("ABC", "reduce")[0].intent.reason == "forced_cover"
    replay.path(candle(opening=120, high=120, low=120, close=120, volume=3), True)
    assert next(iter(replay.portfolio.lots.values())).quantity == 9


def test_signed_ledger_partial_cover_releases_proportional_collateral() -> None:
    from turtle_trader.domain import Fill
    portfolio = Portfolio(D(1000))
    portfolio.apply(Fill("1", "ABC", "sell", 10, D(10), D(0), START, "entry",
                         direction=-1, unit_id="u"))
    assert portfolio.equity == 1000 and portfolio.available_cash == 950
    portfolio.apply(Fill("2", "ABC", "buy", 4, D(12), D(0), START, "cover",
                         direction=-1, intent="reduce", unit_id="u"))
    assert portfolio.collateral == 90
    assert portfolio.realized_pnl == -8


def test_no_current_session_lookahead() -> None:
    replay = warmed()
    levels = replay.strategies["ABC"].levels()
    replay.path(candle(high=120, low=100, close=110), False)
    assert replay.strategies["ABC"].levels() == levels


def test_input_order_and_worker_equivalent_engine_determinism() -> None:
    bars = [candle(i, symbol=s) for i in range(60) for s in ("XYZ", "ABC")]
    config = StrategyConfig(entry_length=2, exit_length=2, atr_length=2)
    a = run_backtest(bars, config, RiskConfig(), FREE, FixtureCalendar())
    b = run_backtest(list(reversed(bars)), config, RiskConfig(), FREE, FixtureCalendar())
    assert a == b


def test_complete_exchange_sessions_and_internal_gap_detection() -> None:
    class Calendar(FixtureCalendar):
        def bounds(self, day: date, market_hours: str) -> tuple[datetime, datetime]:
            start = datetime.combine(day, START.timetz())
            return start, start + timedelta(minutes=10)
    bars = [candle(0), candle(1), candle(2)]
    with pytest.raises(ValueError, match="incomplete internal"):
        run_backtest(bars, StrategyConfig(), RiskConfig(), FREE, Calendar())


@pytest.mark.parametrize("field,value", [("volume", -1), ("high", 90), ("low", 110)])
def test_invalid_bars_rejected(field: str, value: float) -> None:
    from dataclasses import replace
    with pytest.raises(ValueError, match="invalid OHLCV"):
        run_backtest([replace(candle(), **{field: value})], StrategyConfig(), RiskConfig(),
                     FREE, FixtureCalendar())
