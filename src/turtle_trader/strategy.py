"""Confirmed-bar translation of Turtle Breakout - Revelio's state machine.

Signal validity and scheduled exposure are separate: a signal can persist while
flat, and an entry or re-entry can also exit within the same confirmed candle.
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from turtle_trader.domain import Bar, StrategyConfig, StrategyEvents
from turtle_trader.indicators import WilderATR, true_range
from turtle_trader.sessions import allowed, deadline_for, local_time, session_start


@dataclass
class StrategyState:
    session_start: datetime | None = None
    daily_high: float | None = None
    daily_low: float | None = None
    daily_close: float | None = None
    previous_close: float | None = None
    skip_first: bool = True
    highs: deque[float] = field(default_factory=deque)
    lows: deque[float] = field(default_factory=deque)
    atr: float | None = None
    signal_long: bool = False
    held: bool = False
    stop: float | None = None
    deadline: datetime | None = None
    signal_marked: bool = False
    signal_start: datetime | None = None
    signal_price: float | None = None
    last_held_time: datetime | None = None
    last_held_price: float | None = None


class Strategy:
    def __init__(self, config: StrategyConfig) -> None:
        self.config = config
        self.state = StrategyState(
            highs=deque(maxlen=config.entry_length), lows=deque(maxlen=config.exit_length)
        )
        self._atr = WilderATR(config.atr_length)

    def _observe(self, bar: Bar) -> None:
        state = self.state
        start = session_start(bar.time)
        if start != state.session_start:
            if state.daily_high is not None:
                assert state.daily_low is not None and state.daily_close is not None
                if state.skip_first:
                    state.skip_first = False
                else:
                    state.highs.append(state.daily_high)
                    state.lows.append(state.daily_low)
                    state.atr = self._atr.update(
                        true_range(state.daily_high, state.daily_low, state.previous_close)
                    )
                    state.previous_close = state.daily_close
            state.session_start = start
            state.daily_high, state.daily_low = bar.high, bar.low
        else:
            assert state.daily_high is not None and state.daily_low is not None
            state.daily_high = max(state.daily_high, bar.high)
            state.daily_low = min(state.daily_low, bar.low)
        state.daily_close = bar.close

    def on_bar(self, bar: Bar, *, confirmed: bool = True) -> StrategyEvents:
        """Observe one bar. Unconfirmed previews never change persistent state.

        Feed all observed candles in chronological order, including outside
        execution hours. Levels use completed 17:00 sessions only.
        """
        local_time(bar.time)
        local_time(bar.time_close)
        if confirmed:
            self._observe(bar)
        state, config = self.state, self.config
        entry_level = max(state.highs) if len(state.highs) == config.entry_length else None
        exit_level = min(state.lows) if len(state.lows) == config.exit_length else None
        entry = reentry = scheduled_close = signal_exit = exit_while_flat = False
        entry_mark = exit_mark = signal_exit_price = None
        shade_held = state.held and (
            not config.day_trading or (state.deadline is not None and bar.time < state.deadline)
        )

        if (
            confirmed and entry_level is not None
            and exit_level is not None and state.atr is not None
        ):
            was_long = state.signal_long
            breakout = not was_long and bar.high > entry_level
            if breakout:
                state.signal_marked = False
                state.stop = max(entry_level, bar.open) - config.atr_multiple * state.atr
                state.signal_long = True
                signal_exit = bar.low < state.stop
            elif was_long:
                assert state.stop is not None
                signal_exit = bar.low < max(state.stop, exit_level)
            if signal_exit:
                assert state.stop is not None
                signal_exit_price = (
                    min(bar.open, max(state.stop, exit_level)) if was_long else state.stop
                )
                state.signal_long = False

            if (
                config.day_trading and state.held
                and state.deadline is not None and bar.time >= state.deadline
            ):
                state.last_held_time, state.last_held_price = bar.time, bar.open
                state.held = False
                scheduled_close = True
            if not state.held and allowed(bar.time, config):
                entry = breakout
                reentry = was_long and state.stop is not None and (
                    bar.open >= max(state.stop, exit_level)
                )
                if entry or reentry:
                    if not state.signal_marked:
                        entry_mark = max(entry_level, bar.open) if breakout else bar.open
                        state.signal_start, state.signal_price = bar.time, entry_mark
                        state.signal_marked = True
                    state.held = True
                    state.deadline = deadline_for(bar.time, config)

            shade_held = state.held
            if state.held:
                state.last_held_time = bar.time
                state.last_held_price = signal_exit_price if signal_exit else bar.close
                if signal_exit:
                    state.held = False
                elif config.day_trading and bar.time_close == state.deadline:
                    state.held = False
                    scheduled_close = True
            if signal_exit and state.signal_marked:
                assert state.stop is not None
                exit_while_flat = not shade_held
                exit_mark = max(state.stop, exit_level) if exit_while_flat else signal_exit_price
                state.signal_marked = False

        active_stop = (
            max(state.stop, exit_level)
            if state.signal_long and state.stop is not None and exit_level is not None else None
        )
        return StrategyEvents(
            symbol=bar.symbol, time=bar.time, known_at=bar.time_close,
            entry_level=entry_level, exit_level=exit_level, atr=state.atr,
            initial_stop=state.stop, active_stop=active_stop,
            signal_long=state.signal_long, held=state.held,
            entry=entry, reentry=reentry, scheduled_close=scheduled_close, signal_exit=signal_exit,
            entry_mark=entry_mark, exit_mark=exit_mark, signal_exit_price=signal_exit_price,
            shade_held=shade_held, deadline=state.deadline, exit_while_flat=exit_while_flat,
        )
