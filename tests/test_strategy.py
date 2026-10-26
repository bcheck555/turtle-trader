"""Hand-calculated prospective rules and independent shadow campaigns."""

from dataclasses import replace

import pytest

from turtle_trader.domain import StrategyConfig
from turtle_trader.indicators import WilderATR, true_range
from turtle_trader.strategy import (
    Strategy,
    TurtleChannelExit,
    TurtleEntry,
    register_entry,
    register_exit,
)


def warmed(**kwargs: object) -> Strategy:
    strategy = Strategy(StrategyConfig(**kwargs))  # type: ignore[arg-type]
    for _ in range(55):
        strategy.update_session(110, 100, 105)
    strategy.publish_week()
    return strategy


def test_true_range_and_n_seed_then_smoothing() -> None:
    assert true_range(12, 9, None) == 3
    assert true_range(12, 9, 7) == 5
    assert true_range(12, 9, 15) == 6
    atr = WilderATR(3)
    assert atr.update(3) is None
    assert atr.update(6) is None
    assert atr.update(9) == 6
    assert atr.update(12) == 8


def test_defaults_and_system_specific_lengths() -> None:
    assert StrategyConfig().entry_length == 55
    assert StrategyConfig().exit_length == 20
    s1 = StrategyConfig(entry_strategy="turtle_s1")
    assert (s1.entry_length, s1.exit_length) == (20, 10)
    assert not s1.allow_short


def test_channels_use_completed_samples_and_one_tick_beyond_boundary() -> None:
    strategy = Strategy(StrategyConfig())
    for _ in range(54):
        strategy.update_session(110, 100, 105)
    strategy.publish_week()
    assert strategy.levels().entry_long is None
    strategy.update_session(110, 100, 105)
    levels = strategy.levels()
    assert levels.entry_long == 110.01
    assert levels.entry_short == 99.99
    assert levels.exit_long == 99.99
    assert levels.exit_short == 110.01


def test_daily_n_does_not_change_published_weekly_n() -> None:
    strategy = warmed()
    assert strategy.levels().n == 10
    strategy.update_session(150, 100, 125)
    assert strategy.n == 12
    assert strategy.levels().n == 10
    assert strategy.publish_week() == 12
    assert strategy.levels().n == 12


def test_winner_filter_uses_failsafe_until_losing_shadow_breakout() -> None:
    strategy = warmed(entry_strategy="turtle_s1")
    for _ in range(20):
        strategy.update_session(108, 102, 105)
    assert strategy.levels().entry_long == 108.01
    strategy.shadow_entry(-1, 100, 10)
    assert strategy.shadow_close(90) is True
    assert strategy.levels().entry_long == 110.01
    assert strategy.shadow_triggers() == (108.01, 101.99)
    strategy.shadow_entry(1, 108.01, 10)
    assert strategy.shadow_close(88.01) is False
    assert strategy.levels().entry_long == 108.01


def test_shadow_stop_and_channel_independent_of_selected_exit_and_parameters() -> None:
    strategy = warmed(entry_strategy="turtle_s1", exit_strategy="chandelier",
                      atr_multiple=4.0, exit_length=3)
    strategy.shadow_entry(1, 130, 5)
    assert strategy.shadow_exit_level() == 120
    strategy.update_session(150, 140, 145)
    assert strategy.shadow_exit_level() == 120
    assert strategy.shadow_close(130) is False
    assert strategy.shadow_direction == 0
    assert strategy.shadow_exit_level() is None


def test_shadow_channels_keep_both_directions_when_shorts_disabled() -> None:
    strategy = warmed(entry_strategy="turtle_s1", allow_short=False)
    assert strategy.shadow_triggers() == (110.01, 99.99)
    strategy.shadow_entry(-1, 99.99, 10)
    assert strategy.shadow_exit_level() == 110.01
    assert strategy.shadow_triggers() == (None, None)
    assert strategy.shadow_close(95) is True


def test_rule_factories_can_be_added_without_execution_changes() -> None:
    register_entry("test_entry", TurtleEntry)
    register_exit("test_exit", TurtleChannelExit)
    strategy = Strategy(StrategyConfig(entry_strategy="test_entry", exit_strategy="test_exit",
                                       entry_length=2, exit_length=2, atr_length=2))
    strategy.update_session(110, 100, 105)
    strategy.update_session(112, 102, 107)
    strategy.publish_week()
    assert strategy.levels().entry_long == 112.01
    with pytest.raises(ValueError, match="already registered"):
        register_entry("test_entry", TurtleEntry)
    with pytest.raises(ValueError, match="explicit exit_length"):
        replace(StrategyConfig(), entry_strategy="test_entry", exit_length=0)


@pytest.mark.parametrize("field,value", [
    ("entry_length", True), ("exit_length", 1), ("atr_length", 1),
    ("interval_minutes", True), ("allow_short", 1), ("atr_multiple", float("nan")),
    ("add_multiple", 0), ("chandelier_ratchet", "false"),
])
def test_invalid_rule_parameters_rejected(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        StrategyConfig(**{field: value})  # type: ignore[arg-type]
