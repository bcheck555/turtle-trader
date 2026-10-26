from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import pytest

from turtle_trader.domain import Bar, StrategyConfig
from turtle_trader.sessions import NEW_YORK
from turtle_trader.strategy import Strategy


def bar(
    value: str, *, opening: float = 9, high: float = 10, low: float = 8, close: float = 9,
) -> Bar:
    at = datetime.fromisoformat(value).replace(tzinfo=NEW_YORK)
    return Bar("ABC", at, at + timedelta(minutes=5), opening, high, low, close, 100)


def config(**kwargs: object) -> StrategyConfig:
    values: dict[str, object] = dict(
        entry_length=2, exit_length=2, atr_length=2, atr_multiple=2,
        exit_strategy="chandelier", chandelier_timeframe="intraday",
        chandelier_length=2, chandelier_atr_length=2, chandelier_multiple=1,
        day_trading=False,
    )
    values.update(kwargs)
    return StrategyConfig(**values)  # type: ignore[arg-type]


def warmed(**kwargs: object) -> Strategy:
    strategy = Strategy(config(**kwargs))
    for day in (4, 5, 6):
        strategy.on_bar(bar(f"2026-01-{day:02}T09:00"))
    return strategy


def enter(strategy: Strategy) -> None:
    event = strategy.on_bar(bar("2026-01-07T10:00", opening=10, high=12, low=9, close=11))
    assert event.entry and event.held and event.initial_stop == 6
    assert event.stop_used == 6 and event.active_stop == 9.5


def test_intraday_new_line_does_not_retroactively_test_current_low() -> None:
    strategy = warmed(exit_length=400)
    enter(strategy)
    event = strategy.on_bar(bar("2026-01-07T10:05", opening=11, high=20, low=10, close=18))
    assert event.exit_level is None  # Unused Revelio exit band never gates CE readiness.
    assert event.stop_used == 9.5 and event.proposed_stop == 13.75
    assert event.chandelier_atr == 6.25 and event.chandelier_level == 13.75
    assert not event.signal_exit and event.held and event.active_stop == 13.75
    event = strategy.on_bar(bar("2026-01-07T10:10", opening=18, high=19, low=13, close=17))
    assert event.stop_used == event.signal_exit_price == 13.75
    assert event.signal_exit and event.exit_reason == "stop"
    assert event.proposed_stop == 13.875  # Candidate remains visible even on terminal bars.
    assert not event.signal_long and not event.held and event.active_stop is None


def test_new_line_close_breach_exits_at_close_after_incoming_stop_passes() -> None:
    strategy = warmed()
    enter(strategy)
    event = strategy.on_bar(bar("2026-01-07T10:05", opening=11, high=30, low=10, close=11))
    assert event.stop_used == 9.5 and event.proposed_stop == 18.75
    assert event.chandelier_atr == 11.25 and event.chandelier_stop == 18.75
    assert event.signal_exit and event.exit_reason == "new_stop_close_breach"
    assert event.signal_exit_price == event.exit_mark == 11
    assert event.shade_held and not event.held and not event.signal_long
    assert strategy.state.chandelier_stop is None and strategy.state.published_stop is None


def test_breakout_bar_initial_only_then_can_close_breach_new_line() -> None:
    strategy = warmed()
    event = strategy.on_bar(bar("2026-01-07T10:00", opening=10, high=30, low=9, close=10))
    assert event.entry and event.signal_exit and event.stop_used == 6
    assert event.proposed_stop == 18.5 and event.signal_exit_price == 10
    assert event.exit_reason == "new_stop_close_breach" and event.shade_held
    assert event.entry_mark == event.exit_mark == 10
    assert not event.held and not event.signal_long


def test_breakout_initial_stop_exit_takes_precedence_over_close_candidate() -> None:
    strategy = warmed()
    event = strategy.on_bar(bar("2026-01-07T10:00", opening=10, high=30, low=5, close=10))
    assert event.entry and event.signal_exit and event.stop_used == 6
    assert event.exit_reason == "stop" and event.signal_exit_price == 6
    assert event.proposed_stop == 16.5


def test_strict_stop_and_close_equality_and_gap_price() -> None:
    strategy = warmed(chandelier_multiple=2)
    event = strategy.on_bar(bar("2026-01-07T10:00", high=11, low=8, close=9))
    # CE ATR=2.5, raw=6.0, equal to the initial floor.
    assert event.active_stop == event.initial_stop == 6
    event = strategy.on_bar(bar("2026-01-07T10:05", opening=6, high=9, low=6, close=6))
    assert not event.signal_exit and event.stop_used == event.proposed_stop == 6
    event = strategy.on_bar(bar("2026-01-07T10:10", opening=5, high=7, low=4, close=6))
    assert event.signal_exit and event.signal_exit_price == 5 and event.exit_reason == "stop"


@pytest.mark.parametrize("ratchet", [True, False])
def test_raw_fall_optional_ratchet_and_floor(ratchet: bool) -> None:
    strategy = warmed(chandelier_ratchet=ratchet)
    sequence = [
        (20, 9, 19), (19, 17, 18), (18, 16, 17), (17, 16, 16.5), (16.8, 16, 16.2),
    ]
    events = [strategy.on_bar(bar(f"2026-01-07T10:{n * 5:02}",
                                 opening=10 if n == 0 else close,
                                 high=high, low=low, close=close))
              for n, (high, low, close) in enumerate(sequence)]
    assert all(event.signal_long for event in events)
    event = events[-1]
    assert event.chandelier_level == pytest.approx(15.56875)
    assert event.chandelier_stop == pytest.approx(15.9375 if ratchet else 15.56875)
    assert event.active_stop == event.proposed_stop == event.chandelier_stop
    assert all(event.initial_stop == 6 for event in events)


def test_initial_floor_kept_when_unratcheted_raw_falls_below_it() -> None:
    strategy = warmed(chandelier_multiple=10, chandelier_ratchet=False)
    event = strategy.on_bar(bar("2026-01-07T10:00", opening=10, high=12, low=9, close=11))
    assert event.chandelier_level == -13 and event.chandelier_stop == -13
    assert event.initial_stop == event.proposed_stop == event.active_stop == 6


def test_trade_ratchet_resets_but_indicator_history_persists_after_signal_exit() -> None:
    strategy = warmed()
    sequence = [
        (20, 9, 19), (19, 17, 18), (18, 16, 17), (17, 16, 16.5), (16.8, 16, 16.2),
    ]
    for n, (high, low, close) in enumerate(sequence):
        previous = strategy.on_bar(bar(f"2026-01-07T10:{n * 5:02}",
                                       opening=10 if n == 0 else close,
                                       high=high, low=low, close=close))
    assert previous.chandelier_stop == 15.9375
    event = strategy.on_bar(bar("2026-01-07T10:25", opening=16, high=17, low=15, close=16))
    assert event.signal_exit and strategy.state.chandelier_stop is None
    flat = strategy.on_bar(bar("2026-01-07T10:30", opening=9, high=9, low=8, close=8.5))
    assert not flat.signal_long and flat.chandelier_stop is None
    assert flat.chandelier_level is not None and flat.chandelier_level != event.chandelier_level
    new = strategy.on_bar(bar("2026-01-07T10:35", opening=10, high=21, low=6.1, close=20))
    assert new.entry and new.signal_long
    assert new.chandelier_stop == new.chandelier_level
    assert new.chandelier_stop is not None and new.chandelier_stop < 15.9375


def test_scheduled_flat_keeps_ratchet_and_reentry_can_postclose_exit() -> None:
    strategy = warmed(day_trading=True)
    strategy.on_bar(bar("2026-01-07T10:00", opening=10, high=20, low=9, close=19))
    closed = strategy.on_bar(bar("2026-01-07T16:25", opening=18, high=19, low=17, close=18))
    assert closed.scheduled_close and not closed.held and closed.signal_long
    assert strategy.state.chandelier_stop == 15.75
    flat = strategy.on_bar(bar("2026-01-07T17:55", opening=17, high=18, low=16, close=17))
    assert not flat.held and flat.signal_long and flat.active_stop == 15.875
    event = strategy.on_bar(bar("2026-01-07T18:00", opening=17, high=30, low=16, close=17))
    assert event.reentry and event.signal_exit and not event.held
    assert event.stop_used == 15.875 and event.exit_reason == "new_stop_close_breach"
    assert event.signal_exit_price == 17 and event.entry_mark is None


def test_close_breach_ends_underlying_signal_during_scheduled_flat_period() -> None:
    strategy = warmed(day_trading=True)
    strategy.on_bar(bar("2026-01-07T10:00", opening=10, high=20, low=9, close=19))
    strategy.on_bar(bar("2026-01-07T16:25", opening=18, high=19, low=17, close=18))
    event = strategy.on_bar(bar("2026-01-07T17:00", opening=17, high=30, low=16, close=17))
    assert event.signal_exit and not event.held and not event.signal_long
    assert event.exit_reason == "new_stop_close_breach" and event.signal_exit_price == 17
    assert event.exit_while_flat and event.exit_mark == 17


def test_missed_schedule_closes_and_reenters_without_resetting_ratchet() -> None:
    strategy = warmed(day_trading=True)
    strategy.on_bar(bar("2026-01-07T10:00", opening=10, high=20, low=9, close=19))
    event = strategy.on_bar(bar("2026-01-07T18:00", opening=18, high=19, low=17, close=18))
    assert event.scheduled_close and event.reentry and event.held
    assert event.stop_used == 13.5 and event.chandelier_stop == 15.75


def test_session_line_first_published_after_confirmation_never_retrotests_low() -> None:
    strategy = warmed(chandelier_timeframe="session")
    event = strategy.on_bar(bar("2026-01-07T10:00", opening=10, high=20, low=9, close=19))
    assert event.chandelier_level == event.active_stop == 8 and event.stop_used == 6
    event = strategy.on_bar(bar("2026-01-07T17:00", opening=10, high=14, low=9, close=14))
    assert event.chandelier_atr == 6.5 and event.chandelier_level == 13.5
    assert event.stop_used == 8 and event.active_stop == 13.5 and not event.signal_exit
    # Gap below the previously published threshold exits at the next open.
    event = strategy.on_bar(bar("2026-01-07T17:05", opening=12, high=14, low=11, close=13))
    assert event.signal_exit and event.signal_exit_price == 12 and event.stop_used == 13.5


def test_session_new_stop_close_breach_and_first_session_discarded() -> None:
    strategy = Strategy(config(chandelier_timeframe="session"))
    strategy.on_bar(bar("2026-01-04T09:00", opening=500, high=1000, low=1, close=999))
    strategy.on_bar(bar("2026-01-05T09:00"))
    strategy.on_bar(bar("2026-01-06T09:00"))
    event = strategy.on_bar(bar("2026-01-07T10:00", opening=10, high=20, low=9, close=19))
    assert event.chandelier_atr == 2 and event.chandelier_level == 8
    event = strategy.on_bar(bar("2026-01-07T17:00", opening=10, high=14, low=9, close=10))
    assert event.stop_used == 8 and event.proposed_stop == 13.5
    assert event.exit_reason == "new_stop_close_breach" and event.signal_exit_price == 10


@pytest.mark.parametrize(("length", "atr_length"), [(5, 2), (2, 5)])
def test_intraday_warmup_independent_of_session_initial_atr(length: int, atr_length: int) -> None:
    strategy = warmed(chandelier_length=length, chandelier_atr_length=atr_length)
    event = strategy.on_bar(bar("2026-01-07T10:00", high=11, close=10))
    assert event.atr == 2 and not event.entry and event.chandelier_level is None
    event = strategy.on_bar(bar("2026-01-07T10:05", high=11, close=10))
    assert event.entry and event.chandelier_level is not None


def test_initial_session_atr_and_chandelier_intraday_atr_are_independent() -> None:
    strategy = warmed()
    event = strategy.on_bar(bar("2026-01-07T10:00", opening=10, high=12, low=9, close=11))
    assert event.atr == 2 and event.chandelier_atr == 2.5
    event = strategy.on_bar(bar("2026-01-07T10:05", opening=11, high=30, low=10, close=25))
    assert event.atr == 2 and event.chandelier_atr == 11.25


@pytest.mark.parametrize("timeframe", ["session", "intraday"])
def test_unconfirmed_preview_never_mutates_indicator_or_signal(timeframe: str) -> None:
    strategy = warmed(chandelier_timeframe=timeframe)
    strategy.on_bar(bar("2026-01-07T10:00", high=11, close=10))
    before = asdict(strategy.state)
    indicator_before = (
        list(strategy._chandelier.highs), strategy._chandelier.previous_close,
        strategy._chandelier.atr, strategy._chandelier.level,
    )
    event = strategy.on_bar(bar("2026-01-07T17:00", high=100, low=1), confirmed=False)
    assert not event.signal_exit and not event.entry and not event.reentry
    assert asdict(strategy.state) == before
    assert (
        list(strategy._chandelier.highs), strategy._chandelier.previous_close,
        strategy._chandelier.atr, strategy._chandelier.level,
    ) == indicator_before


def test_session_dst_and_missing_weekend_do_not_create_extra_samples() -> None:
    strategy = Strategy(config(chandelier_timeframe="session"))
    for value in ("2026-03-04T18:00", "2026-03-05T18:00", "2026-03-06T18:00"):
        strategy.on_bar(bar(value))
    event = strategy.on_bar(bar("2026-03-08T18:00", high=11, close=10))
    assert event.chandelier_atr == 2 and event.chandelier_level == 8
    assert strategy.state.session_start is not None
    assert strategy.state.session_start.astimezone(UTC).hour == 21
    event = strategy.on_bar(bar("2026-03-09T17:00", high=11, low=8, close=9.5))
    assert event.chandelier_atr == 2.5 and event.stop_used == 8
    assert event.proposed_stop == 8.5 and not event.signal_exit


def test_revelio_default_retains_empty_chandelier_diagnostics() -> None:
    strategy = Strategy(StrategyConfig(entry_length=2, exit_length=2, atr_length=2))
    event = strategy.on_bar(bar("2026-01-05T10:00"))
    assert event.exit_strategy == "revelio"
    assert event.chandelier_level is event.chandelier_stop is event.chandelier_atr is None
    assert event.stop_used is event.proposed_stop is event.exit_reason is None
