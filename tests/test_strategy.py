from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from turtle_trader.domain import Bar, StrategyConfig
from turtle_trader.indicators import WilderATR, true_range
from turtle_trader.sessions import NEW_YORK
from turtle_trader.strategy import Strategy


def bar(
    value: str, *, opening: float = 9, high: float = 10, low: float = 8,
    close: float = 9, minutes: int = 5,
) -> Bar:
    timestamp = datetime.fromisoformat(value).replace(tzinfo=NEW_YORK)
    return Bar("TEST", timestamp, timestamp + timedelta(minutes=minutes),
               opening, high, low, close, 1000)


def warmed(*, day_trading: bool = False, **kwargs: int) -> Strategy:
    strategy = Strategy(StrategyConfig(
        entry_length=2, exit_length=2, atr_length=2, day_trading=day_trading, **kwargs
    ))
    for day in (4, 5, 6):
        strategy.on_bar(bar(f"2026-01-{day:02}T09:00"))
    return strategy


def test_warmup_discards_first_session_and_needs_completed_windows() -> None:
    strategy = Strategy(StrategyConfig(entry_length=2, exit_length=2, atr_length=2))
    strategy.on_bar(bar("2026-01-04T18:00", high=1000, low=1, close=999))
    for value in ("2026-01-05T18:00", "2026-01-06T18:00"):
        event = strategy.on_bar(bar(value))
        assert event.entry_level is None and event.atr is None
        assert not event.entry
    event = strategy.on_bar(bar("2026-01-07T18:00"))
    assert (event.entry_level, event.exit_level, event.atr) == (10, 8, 2)
    assert strategy.state.previous_close == 9
    assert not strategy.state.skip_first


def test_true_range_seed_wilder_and_gaps() -> None:
    assert true_range(12, 8, None) == 4
    assert true_range(20, 17, 11) == 9
    assert true_range(10, 8, 20) == 12
    atr = WilderATR(2)
    assert atr.update(4) is None
    assert atr.update(9) == 6.5
    assert atr.update(2) == 4.25
    assert atr.count == 2


def test_session_ohlc_and_previous_retained_close_drive_atr() -> None:
    strategy = Strategy(StrategyConfig(
        entry_length=2, exit_length=2, atr_length=2, day_trading=False
    ))
    for candle in [
        bar("2026-01-04T18:00", high=1000, low=1, close=999),
        bar("2026-01-05T18:00", high=12, low=8, close=11),
        bar("2026-01-06T18:00", opening=18, high=20, low=17, close=18),
    ]:
        strategy.on_bar(candle)
    event = strategy.on_bar(bar("2026-01-07T18:00", high=19, low=17, close=18))
    assert event.atr == 6.5
    assert event.entry_level == 20
    event = strategy.on_bar(bar("2026-01-08T18:00", high=19, low=17, close=18))
    assert event.atr == 4.25


def test_unconfirmed_bar_cannot_update_session_or_trade() -> None:
    strategy = warmed()
    event = strategy.on_bar(bar("2026-01-07T18:00", high=100), confirmed=False)
    assert event.entry_level is None and event.atr is None and not event.entry
    assert strategy.state.session_start == bar("2026-01-05T17:00").time
    event = strategy.on_bar(bar("2026-01-07T18:00", high=11))
    assert event.entry and event.atr == 2 and event.entry_level == 10


def test_breakout_strict_boundary_and_initial_stop_only() -> None:
    strategy = warmed()
    equal = strategy.on_bar(bar("2026-01-07T18:00"))
    assert not equal.entry
    event = strategy.on_bar(bar("2026-01-07T18:05", high=11, low=7))
    assert event.entry and event.signal_long and event.held
    assert event.initial_stop == 6 and event.active_stop == 8
    assert not event.signal_exit  # 7 is below exit band, but above the new initial stop.
    equal_stop = strategy.on_bar(bar("2026-01-07T18:10", high=11, low=8))
    assert not equal_stop.signal_exit
    exit_event = strategy.on_bar(bar("2026-01-07T18:15", high=11, low=7.9))
    assert exit_event.signal_exit and exit_event.signal_exit_price == 8
    assert exit_event.exit_mark == 8 and exit_event.shade_held
    assert not exit_event.held and not exit_event.signal_long
    assert exit_event.active_stop is None


def test_breakout_and_exit_same_bar_preserve_events_markers() -> None:
    strategy = warmed()
    event = strategy.on_bar(bar("2026-01-07T18:00", high=11, low=5))
    assert event.entry and event.signal_exit and event.shade_held
    assert event.entry_mark == 10 and event.exit_mark == 6
    assert event.signal_exit_price == 6
    assert not event.held and not event.signal_long
    assert not event.exit_while_flat


def test_gap_breakout_stop_uses_higher_open_and_exit_gap_uses_open() -> None:
    strategy = warmed()
    event = strategy.on_bar(bar("2026-01-07T18:00", opening=15, high=16, low=12))
    assert event.entry_mark == 15 and event.initial_stop == 11
    event = strategy.on_bar(bar("2026-01-07T18:05", opening=7, high=9, low=6))
    assert event.signal_exit_price == 7


def test_scheduled_close_preserves_signal_then_reentry_without_duplicate_marker() -> None:
    strategy = warmed(day_trading=True)
    first = strategy.on_bar(bar("2026-01-07T10:00", high=11))
    assert first.entry_mark == 10
    closing = strategy.on_bar(bar("2026-01-07T16:25", high=11))
    assert closing.scheduled_close and closing.shade_held
    assert closing.signal_long and not closing.held
    blocked = strategy.on_bar(bar("2026-01-07T17:55", high=11))
    assert not blocked.reentry and not blocked.held
    reopened = strategy.on_bar(bar("2026-01-07T18:00", high=11))
    assert reopened.reentry and reopened.held and reopened.entry_mark is None
    assert reopened.deadline == bar("2026-01-08T16:30").time


def test_reentry_and_exit_samebar_uses_prior_signal_and_open() -> None:
    strategy = warmed(day_trading=True)
    strategy.on_bar(bar("2026-01-07T10:00", high=11))
    strategy.on_bar(bar("2026-01-07T16:25", high=11))
    event = strategy.on_bar(bar("2026-01-07T18:00", opening=9, high=11, low=7))
    assert event.reentry and event.signal_exit and event.shade_held
    assert event.exit_mark == 8 and event.signal_exit_price == 8
    assert not event.held and not event.signal_long


def test_missed_deadline_closes_and_reenters_same_observed_bar() -> None:
    strategy = warmed(day_trading=True)
    strategy.on_bar(bar("2026-01-07T10:00", high=11))
    event = strategy.on_bar(bar("2026-01-07T18:00", high=11))
    assert event.scheduled_close and event.reentry and event.held
    assert event.shade_held and event.entry_mark is None
    assert event.deadline == bar("2026-01-08T16:30").time


def test_missed_deadline_gap_exit_uses_flat_marker_and_observed_open() -> None:
    strategy = warmed(day_trading=True)
    strategy.on_bar(bar("2026-01-07T10:00", high=11))
    event = strategy.on_bar(bar("2026-01-07T18:00", opening=7, high=9, low=6))
    assert event.scheduled_close and event.signal_exit and not event.reentry
    assert event.signal_exit_price == 7 and event.exit_mark == 8
    assert event.exit_while_flat and not event.shade_held
    assert strategy.state.last_held_price == 7
    assert strategy.state.last_held_time == event.time


def test_signal_exit_takes_precedence_over_exact_scheduled_close() -> None:
    strategy = warmed(day_trading=True)
    strategy.on_bar(bar("2026-01-07T10:00", high=11))
    event = strategy.on_bar(bar("2026-01-07T16:25", high=11, low=7))
    assert event.signal_exit and not event.scheduled_close
    assert event.shade_held and not event.held


def test_reentry_equal_to_active_stop_can_exit_later_in_candle() -> None:
    strategy = warmed(day_trading=True)
    strategy.on_bar(bar("2026-01-07T10:00", high=11))
    strategy.on_bar(bar("2026-01-07T16:25", high=11))
    event = strategy.on_bar(bar("2026-01-07T18:00", opening=8, high=9, low=7))
    assert event.reentry and event.signal_exit
    assert event.signal_exit_price == 8 and not event.held


def test_initial_stop_is_fixed_as_price_and_completed_bands_change() -> None:
    strategy = warmed()
    event = strategy.on_bar(bar("2026-01-07T18:00", high=11, low=9))
    assert event.initial_stop == 6
    event = strategy.on_bar(bar("2026-01-08T18:00", opening=15, high=20, low=12, close=16))
    assert event.initial_stop == 6 and event.active_stop == 8
    event = strategy.on_bar(bar("2026-01-09T18:00", opening=18, high=25, low=15, close=20))
    assert event.initial_stop == 6 and event.active_stop == 9
    assert not event.entry and event.signal_long


def test_break_breakout_becomes_first_marked_reentry_and_flat_exit() -> None:
    strategy = warmed(day_trading=True)
    event = strategy.on_bar(bar("2026-01-07T16:30", high=11))
    assert event.signal_long and not event.entry and not event.held
    assert event.entry_mark is None
    event = strategy.on_bar(bar("2026-01-07T18:00", high=11))
    assert event.reentry and event.entry_mark == 9
    strategy.on_bar(bar("2026-01-08T16:25", high=11))
    event = strategy.on_bar(bar("2026-01-08T17:00", high=11, low=7))
    assert event.signal_exit and event.exit_while_flat and not event.shade_held
    assert event.exit_mark == 8


def test_unmarked_signal_exit_while_flat_has_no_marker() -> None:
    strategy = warmed(day_trading=True)
    strategy.on_bar(bar("2026-01-07T16:30", high=11))
    event = strategy.on_bar(bar("2026-01-07T16:35", high=11, low=7))
    assert event.signal_exit and event.exit_mark is None
    assert not event.exit_while_flat


def test_offhours_bars_update_completed_windows_and_final_session_unfinalized() -> None:
    strategy = warmed(day_trading=True)
    strategy.on_bar(bar("2026-01-07T17:00", high=100, low=1))
    assert list(strategy.state.highs) == [10, 10]
    event = strategy.on_bar(bar("2026-01-08T17:00"))
    assert event.entry_level == 100 and event.exit_level == 1
    assert list(strategy.state.highs) == [10, 100]
    assert list(strategy.state.lows) == [8, 1]


def test_window_capacity_and_distinct_warmup_lengths() -> None:
    strategy = Strategy(StrategyConfig(
        entry_length=3, exit_length=2, atr_length=4, day_trading=False
    ))
    events = [strategy.on_bar(bar(f"2026-01-{day:02}T18:00")) for day in range(1, 8)]
    assert events[3].entry_level is None and events[3].exit_level == 8
    assert events[4].entry_level == 10 and events[4].atr is None
    assert events[5].atr == 2
    assert len(strategy.state.highs) == 3 and len(strategy.state.lows) == 2
    assert all(not event.entry for event in events)


def test_missing_sessions_are_not_synthesized_and_bar_open_sets_session() -> None:
    strategy = Strategy(StrategyConfig(
        entry_length=2, exit_length=2, atr_length=2, day_trading=False
    ))
    strategy.on_bar(bar("2026-01-01T18:00"))
    strategy.on_bar(bar("2026-01-05T16:55", high=12, minutes=5))
    # A bar ending at rollover belongs to the session containing its opening.
    assert strategy.state.session_start == bar("2026-01-04T17:00").time
    strategy.on_bar(bar("2026-01-05T17:00", high=13))
    event = strategy.on_bar(bar("2026-01-12T18:00", high=11))
    assert list(strategy.state.highs) == [12, 13]
    assert event.entry_level == 13 and event.atr == 4.5


@pytest.mark.parametrize("minutes", [1, 2, 3, 5, 6, 10, 15, 30])
def test_events_known_only_at_candle_close(minutes: int) -> None:
    strategy = warmed(interval_minutes=minutes)
    candle = bar("2026-01-07T18:00", high=11, minutes=minutes)
    event = strategy.on_bar(candle)
    assert event.time == candle.time and event.known_at == candle.time_close


def test_active_exit_can_fall_after_breakout_bar_ignored_exit_band() -> None:
    strategy = warmed()
    first = strategy.on_bar(bar("2026-01-07T18:00", high=11, low=7))
    assert first.initial_stop == 6 and first.active_stop == 8
    later = strategy.on_bar(bar("2026-01-08T18:00", high=11, low=8))
    assert later.signal_long and later.initial_stop == 6 and later.active_stop == 7


def test_missed_friday_deadline_then_sunday_reentry() -> None:
    strategy = warmed(day_trading=True)
    entry = strategy.on_bar(bar("2026-01-09T10:00", high=11))
    assert entry.held and entry.deadline == bar("2026-01-09T16:30").time
    reopened = strategy.on_bar(bar("2026-01-11T18:00", high=11))
    assert reopened.scheduled_close and reopened.reentry and reopened.held
    assert reopened.deadline == bar("2026-01-12T16:30").time


def test_unconfirmed_held_overdue_preview_does_not_mutate_state() -> None:
    strategy = warmed(day_trading=True)
    strategy.on_bar(bar("2026-01-07T10:00", high=11))
    before = deepcopy(strategy.state)
    preview = strategy.on_bar(bar("2026-01-07T18:00", high=11, low=1), confirmed=False)
    assert strategy.state == before
    assert preview.held and not preview.shade_held
    assert not any((preview.entry, preview.reentry, preview.scheduled_close, preview.signal_exit))


def test_full_replay_utc_and_ny_equivalent_across_dst() -> None:
    config = StrategyConfig(entry_length=2, exit_length=2, atr_length=2)
    ny, utc = Strategy(config), Strategy(config)
    for day in range(4, 11):
        candle = bar(f"2026-03-{day:02}T18:00", high=11 if day > 7 else 10)
        converted = replace(candle, time=candle.time.astimezone(UTC),
                            time_close=candle.time_close.astimezone(UTC))
        assert ny.on_bar(candle) == utc.on_bar(converted)
