"""A known exchange pause is not an executable price path or an unknown gap."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from turtle_trader.backtest import _sessions, run_backtest
from turtle_trader.data import ExchangeCalendar
from turtle_trader.domain import Bar, ExecutionConfig, RiskConfig, StrategyConfig

HALT_DAY = date(2020, 3, 9)
FIRST_ABSENT = datetime(2020, 3, 9, 13, 35, tzinfo=UTC)
REOPEN_BAR = datetime(2020, 3, 9, 13, 49, tzinfo=UTC)
HALT_START = datetime(2020, 3, 9, 13, 34, 13, tzinfo=UTC)
HALT_END = datetime(2020, 3, 9, 13, 49, 13, tzinfo=UTC)
ONE_MINUTE = timedelta(minutes=1)


def halt_week() -> tuple[list[Bar], ExchangeCalendar]:
    """Five complete sessions; Monday's 14 fully halted minutes have no bars."""
    days = [date(2020, 3, value) for value in (4, 5, 6, 9, 10)]
    calendar = ExchangeCalendar(days[0], days[-1])
    bars: list[Bar] = []
    for day in days:
        bounds = calendar.bounds(day, "regular")
        assert bounds is not None
        at, close = bounds
        while at < close:
            if not FIRST_ABSENT <= at < REOPEN_BAR:
                bars.append(Bar("QQQ", at, at + ONE_MINUTE, 100, 101, 99, 100, 100000))
            at += ONE_MINUTE
    return bars, calendar


def test_complete_internal_halt_session_is_retained_without_fabricating_bars() -> None:
    bars, calendar = halt_week()
    accepted, closes, daily = _sessions(bars, calendar, 1)

    monday = [bar for bar in accepted if bar.time.date() == HALT_DAY]
    assert len(monday) == 376
    assert accepted == bars
    assert not any(FIRST_ABSENT <= bar.time < REOPEN_BAR for bar in accepted)
    assert closes[("QQQ", HALT_DAY)] == datetime(2020, 3, 9, 20, tzinfo=UTC)
    assert daily[("QQQ", HALT_DAY)] == (101, 99, 100)


@pytest.mark.parametrize("missing", [FIRST_ABSENT - ONE_MINUTE, REOPEN_BAR])
def test_missing_tradable_boundary_minute_remains_an_internal_gap(missing: datetime) -> None:
    bars, calendar = halt_week()
    bars = [bar for bar in bars if bar.time != missing]

    with pytest.raises(ValueError, match="incomplete internal exchange session"):
        _sessions(bars, calendar, 1)


def test_fabricated_candle_inside_halt_cannot_make_session_complete() -> None:
    bars, calendar = halt_week()
    bars.append(Bar("QQQ", FIRST_ABSENT, FIRST_ABSENT + ONE_MINUTE,
                    100, 100, 100, 100, 0))
    bars.sort(key=lambda bar: bar.time)

    with pytest.raises(ValueError, match="incomplete internal exchange session"):
        _sessions(bars, calendar, 1)


def test_stop_jumped_during_halt_fills_at_observed_reopen_not_stale_stop() -> None:
    bars, calendar = halt_week()
    priced: list[Bar] = []
    for bar in bars:
        if bar.time.date() < HALT_DAY:
            priced.append(bar)
            continue
        # Enter just before the pause. Price is unseen throughout the missing
        # minutes and first trades again below both channel and protective stop.
        if bar.time == FIRST_ABSENT - ONE_MINUTE:
            price = 102
        elif bar.time >= REOPEN_BAR:
            price = 95
        else:
            price = 100
        priced.append(Bar(bar.symbol, bar.time, bar.time_close,
                          price, price, price, price, bar.volume))

    result = run_backtest(
        priced,
        StrategyConfig(interval_minutes=1, entry_length=2, exit_length=2, atr_length=2),
        RiskConfig(unit_fraction=Decimal("0.001")),
        ExecutionConfig(slippage_bps=Decimal(0)),
        calendar,
    )

    assert len(result.fills) == 2
    entry, exit_fill = result.fills
    assert entry.side == "buy"
    assert entry.time == FIRST_ABSENT - ONE_MINUTE
    assert entry.price == Decimal(102)
    assert exit_fill.side == "sell"
    assert exit_fill.time == HALT_END
    assert exit_fill.price == Decimal(95)
    assert exit_fill.time_precision == "bar_open"
    assert not any(HALT_START <= fill.time < HALT_END for fill in result.fills)
    assert not any(FIRST_ABSENT < snapshot.time < REOPEN_BAR
                   for snapshot in result.equity)
    reopen_publications = [event for event in result.events
                           if event.kind == "levels_published"
                           and event.time == REOPEN_BAR + ONE_MINUTE]
    assert len(reopen_publications) == 1
    assert reopen_publications[0].known_at == REOPEN_BAR + ONE_MINUTE


def test_synthetic_calendar_does_not_retime_bars_inside_historical_market_halt() -> None:
    class SyntheticCalendar:
        def eligible(self, at: datetime, market_hours: str = "regular") -> bool:
            return True

    bars = [Bar("QQQ", datetime(2020, 3, day, 13, 35, tzinfo=UTC),
                datetime(2020, 3, day, 13, 36, tzinfo=UTC),
                100, 101, 99, 100, 100000) for day in (4, 5, 6)]
    bars.append(Bar("QQQ", FIRST_ABSENT, FIRST_ABSENT + ONE_MINUTE,
                    102, 102, 102, 102, 100000))

    result = run_backtest(
        bars,
        StrategyConfig(interval_minutes=1, entry_length=2, exit_length=2, atr_length=2),
        RiskConfig(unit_fraction=Decimal("0.001")),
        ExecutionConfig(slippage_bps=Decimal(0)),
        SyntheticCalendar(),
    )

    assert len(result.fills) == 1
    assert result.fills[0].time == FIRST_ABSENT
    assert result.fills[0].time < bars[-1].time_close
    assert result.events[-1].time == FIRST_ABSENT + ONE_MINUTE
    assert result.events[-1].known_at == FIRST_ABSENT + ONE_MINUTE
