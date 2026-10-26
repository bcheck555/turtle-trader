"""Prospective daily Turtle rules with independently registered entry/exit policies.

The execution engine owns exchange sessions, weekly publication timing, fills,
and OHLC paths. No current candle may affect its own prospective orders.
"""

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from turtle_trader.chandelier import ChandelierExit
from turtle_trader.domain import Bar, StrategyConfig
from turtle_trader.indicators import WilderATR, true_range


@dataclass(frozen=True)
class EntryTriggers:
    long: float | None = None
    short: float | None = None
    failsafe_long: float | None = None
    failsafe_short: float | None = None


@dataclass(frozen=True)
class RuleLevels:
    entry_long: float | None = None
    entry_short: float | None = None
    failsafe_long: float | None = None
    failsafe_short: float | None = None
    exit_long: float | None = None
    exit_short: float | None = None
    n: float | None = None


class EntryRule(Protocol):
    def observe_session(self, high: float, low: float, close: float) -> None: ...
    def triggers(self, previous_winner: bool) -> EntryTriggers: ...
    def on_fill(self, direction: int, price: float, n: float) -> None: ...
    def on_exit(self) -> None: ...


class ExitRule(Protocol):
    def observe_session(self, high: float, low: float, close: float) -> None: ...
    def observe_bar(self, bar: Bar) -> None: ...
    def levels(self) -> tuple[float | None, float | None]: ...
    def on_fill(self, direction: int, price: float, n: float) -> None: ...
    def on_exit(self) -> None: ...


class _Channel:
    def __init__(self, length: int, tick: float) -> None:
        self.highs: deque[float] = deque(maxlen=length)
        self.lows: deque[float] = deque(maxlen=length)
        self.tick = Decimal(str(tick))

    def observe(self, high: float, low: float) -> None:
        self.highs.append(high)
        self.lows.append(low)

    def triggers(self) -> tuple[float | None, float | None]:
        if len(self.highs) < (self.highs.maxlen or 0):
            return None, None
        return (float(Decimal(str(max(self.highs))) + self.tick),
                float(Decimal(str(min(self.lows))) - self.tick))


class TurtleEntry:
    def __init__(self, config: StrategyConfig) -> None:
        self.config = config
        tick = float(config.tick_size)
        self.channel = _Channel(config.entry_length, tick)
        self.failsafe = _Channel(config.failsafe_length, tick)

    def observe_session(self, high: float, low: float, close: float) -> None:
        self.channel.observe(high, low)
        self.failsafe.observe(high, low)

    def triggers(self, previous_winner: bool) -> EntryTriggers:
        long, short = self.channel.triggers()
        failsafe_long, failsafe_short = self.failsafe.triggers()
        if self.config.entry_strategy == "turtle_s1" and previous_winner:
            long, short = failsafe_long, failsafe_short
        return EntryTriggers(long, short, failsafe_long, failsafe_short)

    def on_fill(self, direction: int, price: float, n: float) -> None:
        pass

    def on_exit(self) -> None:
        pass


class TurtleChannelExit:
    def __init__(self, config: StrategyConfig) -> None:
        self.channel = _Channel(config.exit_length, float(config.tick_size))

    def observe_session(self, high: float, low: float, close: float) -> None:
        self.channel.observe(high, low)

    def observe_bar(self, bar: Bar) -> None:
        pass

    def levels(self) -> tuple[float | None, float | None]:
        high, low = self.channel.triggers()
        return low, high

    def on_fill(self, direction: int, price: float, n: float) -> None:
        pass

    def on_exit(self) -> None:
        pass


class ChandelierRule:
    def __init__(self, config: StrategyConfig) -> None:
        self.config = config
        self.indicator = ChandelierExit(config.chandelier_length,
                                        config.chandelier_atr_length,
                                        config.chandelier_multiple)
        self.direction = 0
        self.long_stop: float | None = None
        self.short_stop: float | None = None

    def _update(self, high: float, low: float, close: float) -> None:
        self.indicator.update(high, low, close)
        long, short = self.indicator.level, self.indicator.short_level
        if self.direction == 1 and long is not None:
            self.long_stop = max(self.long_stop, long) if (
                self.config.chandelier_ratchet and self.long_stop is not None
            ) else long
        if self.direction == -1 and short is not None:
            self.short_stop = min(self.short_stop, short) if (
                self.config.chandelier_ratchet and self.short_stop is not None
            ) else short

    def observe_session(self, high: float, low: float, close: float) -> None:
        if self.config.chandelier_timeframe == "session":
            self._update(high, low, close)

    def observe_bar(self, bar: Bar) -> None:
        if self.config.chandelier_timeframe == "intraday":
            self._update(bar.high, bar.low, bar.close)

    def levels(self) -> tuple[float | None, float | None]:
        return self.long_stop, self.short_stop

    def on_fill(self, direction: int, price: float, n: float) -> None:
        if self.direction == 0:
            self.direction = direction
            self.long_stop = self.indicator.level if direction == 1 else None
            self.short_stop = self.indicator.short_level if direction == -1 else None

    def on_exit(self) -> None:
        self.direction = 0
        self.long_stop = self.short_stop = None


ENTRY_RULES: dict[str, Callable[[StrategyConfig], EntryRule]] = {
    "turtle_s1": TurtleEntry,
    "turtle_s2": TurtleEntry,
}
EXIT_RULES: dict[str, Callable[[StrategyConfig], ExitRule]] = {
    "turtle_channel": TurtleChannelExit,
    "chandelier": ChandelierRule,
}


def register_entry(name: str, factory: Callable[[StrategyConfig], EntryRule]) -> None:
    if not name or name in ENTRY_RULES:
        raise ValueError(f"entry rule already registered or unnamed: {name}")
    ENTRY_RULES[name] = factory


def register_exit(name: str, factory: Callable[[StrategyConfig], ExitRule]) -> None:
    if not name or name in EXIT_RULES:
        raise ValueError(f"exit rule already registered or unnamed: {name}")
    EXIT_RULES[name] = factory


class Strategy:
    def __init__(self, config: StrategyConfig) -> None:
        self.config = config
        try:
            self.entry_rule = ENTRY_RULES[config.entry_strategy](config)
            self.exit_rule = EXIT_RULES[config.exit_strategy](config)
        except KeyError as exc:
            raise ValueError(f"unregistered strategy: {exc.args[0]}") from exc
        self._atr = WilderATR(config.atr_length)
        self.previous_close: float | None = None
        self.weekly_n: float | None = None
        self._shadow_entries = _Channel(config.entry_length, float(config.tick_size))
        self._shadow_exits = _Channel(10, float(config.tick_size))
        self.shadow_direction = 0
        self.shadow_price: float | None = None
        self.shadow_n: float | None = None
        self.last_winner = False

    @property
    def n(self) -> float | None:
        return self._atr.value

    def update_session(self, high: float, low: float, close: float) -> None:
        self._atr.update(true_range(high, low, self.previous_close))
        self.previous_close = close
        self.entry_rule.observe_session(high, low, close)
        self.exit_rule.observe_session(high, low, close)
        self._shadow_entries.observe(high, low)
        self._shadow_exits.observe(high, low)

    def publish_week(self) -> float | None:
        self.weekly_n = self.n
        return self.weekly_n

    def update_bar(self, bar: Bar) -> None:
        self.exit_rule.observe_bar(bar)

    def levels(self) -> RuleLevels:
        triggers = self.entry_rule.triggers(self.last_winner)
        long_exit, short_exit = self.exit_rule.levels()
        return RuleLevels(triggers.long, triggers.short, triggers.failsafe_long,
                          triggers.failsafe_short, long_exit, short_exit, self.weekly_n)

    def on_fill(self, direction: int, price: float, n: float) -> None:
        self.entry_rule.on_fill(direction, price, n)
        self.exit_rule.on_fill(direction, price, n)

    def on_exit(self) -> None:
        self.entry_rule.on_exit()
        self.exit_rule.on_exit()

    def shadow_triggers(self) -> tuple[float | None, float | None]:
        if self.config.entry_strategy != "turtle_s1" or self.shadow_direction:
            return None, None
        return self._shadow_entries.triggers()

    def shadow_entry(self, direction: int, price: float, n: float) -> None:
        if direction not in (-1, 1) or n <= 0:
            raise ValueError("shadow entry requires direction +/-1 and positive N")
        self.shadow_direction, self.shadow_price, self.shadow_n = direction, price, n

    def shadow_exit_level(self) -> float | None:
        if not self.shadow_direction or self.shadow_price is None or self.shadow_n is None:
            return None
        # The winner filter is the original rule even for parameterized/alternate exits.
        stop = self.shadow_price - self.shadow_direction * 2.0 * self.shadow_n
        high, low = self._shadow_exits.triggers()
        if self.shadow_direction == 1:
            return max(stop, low) if low is not None else stop
        return min(stop, high) if high is not None else stop

    def shadow_close(self, price: float) -> bool:
        if not self.shadow_direction or self.shadow_price is None:
            raise ValueError("no active shadow campaign")
        self.last_winner = (price - self.shadow_price) * self.shadow_direction > 0
        self.shadow_direction = 0
        self.shadow_price = self.shadow_n = None
        return self.last_winner
