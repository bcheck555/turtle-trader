"""Chandelier publication is prospective and symmetric."""

from datetime import UTC, datetime, timedelta

from turtle_trader.domain import Bar, StrategyConfig
from turtle_trader.strategy import ChandelierRule, Strategy


def bar(high: float, low: float, close: float) -> Bar:
    time = datetime(2026, 1, 5, 15, tzinfo=UTC)
    return Bar("AAA", time, time + timedelta(minutes=5), close, high, low, close, 100)


def rule(*, ratchet: bool = True, timeframe: str = "session") -> ChandelierRule:
    return ChandelierRule(StrategyConfig(exit_strategy="chandelier", chandelier_length=2,
                                         chandelier_atr_length=2, chandelier_multiple=1.0,
                                         chandelier_ratchet=ratchet,
                                         chandelier_timeframe=timeframe))


def test_daily_rule_warms_before_campaign_and_resets_only_campaign_ratchet() -> None:
    exit_rule = rule()
    exit_rule.observe_session(110, 100, 105)
    exit_rule.observe_session(112, 102, 107)
    assert exit_rule.levels() == (None, None)
    exit_rule.on_fill(1, 113, 10)
    assert exit_rule.levels() == (102, None)
    exit_rule.observe_session(120, 110, 115)
    assert exit_rule.levels() == (108.5, None)
    exit_rule.on_fill(1, 118, 10)
    assert exit_rule.levels() == (108.5, None)
    exit_rule.on_exit()
    assert exit_rule.levels() == (None, None)
    assert exit_rule.indicator.atr == 11.5


def test_short_ratchet_moves_down_and_never_up() -> None:
    exit_rule = rule()
    exit_rule.observe_session(110, 100, 105)
    exit_rule.observe_session(112, 102, 107)
    exit_rule.on_fill(-1, 99, 10)
    assert exit_rule.levels() == (None, 110)
    exit_rule.observe_session(100, 90, 95)
    assert exit_rule.levels() == (None, 103.5)
    exit_rule.observe_session(150, 140, 145)
    assert exit_rule.levels() == (None, 103.5)


def test_intraday_observations_publish_only_when_explicitly_observed() -> None:
    exit_rule = rule(timeframe="intraday")
    exit_rule.observe_session(1000, 1, 100)
    assert exit_rule.indicator.atr is None
    exit_rule.observe_bar(bar(110, 100, 105))
    exit_rule.observe_bar(bar(112, 102, 107))
    exit_rule.on_fill(1, 113, 10)
    incoming = exit_rule.levels()
    assert incoming == (102, None)
    candle = bar(120, 110, 115)
    assert exit_rule.levels() == incoming
    exit_rule.observe_bar(candle)
    assert exit_rule.levels() == (108.5, None)


def test_raw_line_can_fall_when_ratchet_disabled() -> None:
    exit_rule = rule(ratchet=False)
    exit_rule.observe_session(110, 100, 105)
    exit_rule.observe_session(112, 102, 107)
    exit_rule.on_fill(1, 113, 10)
    exit_rule.observe_session(108, 98, 103)
    exit_rule.observe_session(105, 95, 100)
    assert exit_rule.levels() == (98, None)


def test_strategy_notifies_exit_without_erasing_indicator_history() -> None:
    strategy = Strategy(StrategyConfig(exit_strategy="chandelier", chandelier_length=2,
                                       chandelier_atr_length=2, chandelier_multiple=1.0))
    strategy.update_session(110, 100, 105)
    strategy.update_session(112, 102, 107)
    strategy.on_fill(1, 113, 10)
    assert strategy.levels().exit_long == 102
    strategy.on_exit()
    assert strategy.levels().exit_long is None
    strategy.on_fill(-1, 99, 10)
    assert strategy.levels().exit_short == 110
