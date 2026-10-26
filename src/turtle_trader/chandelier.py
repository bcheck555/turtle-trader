"""Independent rolling-high/Wilder-ATR Chandelier indicator.

The caller chooses completed sessions or confirmed intraday bars as samples.
Trade-specific ratcheting and publication timing belong to the strategy.
"""

from collections import deque
from math import isfinite

from turtle_trader.indicators import WilderATR, true_range


class ChandelierExit:
    def __init__(self, length: int, atr_length: int, multiple: float) -> None:
        if any(type(value) is not int or not 2 <= value <= 400
               for value in (length, atr_length)):
            raise ValueError("Chandelier lengths must be integers in 2..400")
        if not isfinite(multiple) or multiple <= 0:
            raise ValueError("Chandelier multiple must be finite and positive")
        self.highs: deque[float] = deque(maxlen=length)
        self.multiple = multiple
        self._atr = WilderATR(atr_length)
        self.previous_close: float | None = None
        self.level: float | None = None

    @property
    def atr(self) -> float | None:
        return self._atr.value

    def update(self, high: float, low: float, close: float) -> float | None:
        self.highs.append(high)
        atr = self._atr.update(true_range(high, low, self.previous_close))
        self.previous_close = close
        if len(self.highs) == self.highs.maxlen and atr is not None:
            self.level = max(self.highs) - self.multiple * atr
        return self.level
