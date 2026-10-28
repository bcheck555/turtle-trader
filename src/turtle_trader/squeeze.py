"""Completed-bar LazyBear Squeeze Momentum with optional ADX/WMA confirmation.

The corrected BB multiplier is used. Keltner width uses SMA true range, whereas
the independently seeded risk ATR and ADX use Wilder smoothing. Signals carry
their publication time and ATR; execution must happen on a later eligible bar.
"""

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from math import fsum, sqrt

from turtle_trader.domain import Bar, StrategyConfig
from turtle_trader.indicators import WilderATR, true_range


@dataclass(frozen=True)
class SqueezeSignal:
    direction: int
    known_at: datetime
    atr: float


class WilderADX:
    """DMI from completed price changes; SMA seeds each Wilder average.

For lengths 14/14, the first ADX is available at the 28th OHLC observation.
Zero smoothed true range or zero summed DIs produces zero DX, never NaN.
"""

    def __init__(self, length: int, smoothing: int) -> None:
        self._tr = WilderATR(length)
        self._plus = WilderATR(length)
        self._minus = WilderATR(length)
        self._dx = WilderATR(smoothing)
        self.previous: tuple[float, float, float] | None = None
        self.value: float | None = None

    def update(self, high: float, low: float, close: float) -> float | None:
        if self.previous is not None:
            previous_high, previous_low, previous_close = self.previous
            up, down = high - previous_high, previous_low - low
            tr = self._tr.update(true_range(high, low, previous_close))
            plus = self._plus.update(up if up > down and up > 0 else 0.0)
            minus = self._minus.update(down if down > up and down > 0 else 0.0)
            if tr is not None and plus is not None and minus is not None:
                total = plus + minus
                dx = 100.0 * abs(plus - minus) / total if total > 0 and tr > 0 else 0.0
                self.value = self._dx.update(dx)
        self.previous = high, low, close
        return self.value


def linear_regression_endpoint(values: deque[float]) -> float:
    """OLS fitted value at x=n-1, with x=0 the oldest sample."""
    length = len(values)
    mean_x = (length - 1) / 2
    mean_y = fsum(values) / length
    numerator = fsum((i - mean_x) * (value - mean_y) for i, value in enumerate(values))
    denominator = length * (length * length - 1) / 12
    return mean_y + numerator / denominator * mean_x


class SqueezeIndicator:
    def __init__(self, config: StrategyConfig) -> None:
        self.config = config
        self._bb: deque[float] = deque(maxlen=config.squeeze_bb_length)
        self._kc: deque[float] = deque(maxlen=config.squeeze_kc_length)
        self._ranges: deque[float] = deque(maxlen=config.squeeze_kc_length)
        self._highs: deque[float] = deque(maxlen=config.squeeze_momentum_length)
        self._lows: deque[float] = deque(maxlen=config.squeeze_momentum_length)
        self._closes: deque[float] = deque(maxlen=config.squeeze_momentum_length)
        self._detrended: deque[float] = deque(maxlen=config.squeeze_momentum_length)
        self._wma: deque[float] = deque(maxlen=config.squeeze_wma_length)
        self._atr = WilderATR(config.squeeze_atr_length)
        self._adx = WilderADX(config.squeeze_adx_length, config.squeeze_adx_smoothing)
        self._previous_close: float | None = None
        self.atr: float | None = None
        self.momentum: float | None = None
        self.adx: float | None = None
        self.wma: float | None = None
        self.state = "warming"

    @property
    def squeeze_on(self) -> bool:
        return self.state == "on"

    @property
    def squeeze_off(self) -> bool:
        return self.state == "off"

    def update(self, bar: Bar) -> SqueezeSignal | None:
        config = self.config
        previous_state = self.state
        tr = true_range(bar.high, bar.low, self._previous_close)
        self._previous_close = bar.close
        self.atr = self._atr.update(tr)
        self.adx = self._adx.update(bar.high, bar.low, bar.close)
        for window in (self._bb, self._kc, self._closes, self._wma):
            window.append(bar.close)
        self._ranges.append(tr)
        self._highs.append(bar.high)
        self._lows.append(bar.low)
        if len(self._wma) == config.squeeze_wma_length:
            length = config.squeeze_wma_length
            self.wma = fsum((i + 1) * value for i, value in enumerate(self._wma)) / (
                length * (length + 1) / 2)
        if len(self._closes) == config.squeeze_momentum_length:
            midpoint = (max(self._highs) + min(self._lows)) / 2
            baseline = (midpoint + fsum(self._closes) / len(self._closes)) / 2
            self._detrended.append(bar.close - baseline)
            if len(self._detrended) == config.squeeze_momentum_length:
                self.momentum = linear_regression_endpoint(self._detrended)
        if len(self._bb) < config.squeeze_bb_length or len(self._kc) < config.squeeze_kc_length:
            return None
        basis = fsum(self._bb) / len(self._bb)
        deviation = sqrt(fsum((value - basis) ** 2 for value in self._bb) / len(self._bb))
        bb_width = config.squeeze_bb_multiple * deviation
        kc_basis = fsum(self._kc) / len(self._kc)
        kc_width = config.squeeze_kc_multiple * fsum(self._ranges) / len(self._ranges)
        if basis - bb_width > kc_basis - kc_width and basis + bb_width < kc_basis + kc_width:
            self.state = "on"
        elif basis - bb_width < kc_basis - kc_width and basis + bb_width > kc_basis + kc_width:
            self.state = "off"
        else:
            self.state = "neutral"
        if previous_state != "on" or not self.squeeze_off:
            return None
        if self.atr is None or self.atr <= 0 or self.momentum is None or self.momentum == 0:
            return None
        direction = 1 if self.momentum > 0 else -1
        if config.squeeze_use_adx and (
            self.adx is None or self.adx <= config.squeeze_adx_threshold
        ):
            return None
        if config.squeeze_use_wma and (self.wma is None or direction * (bar.close - self.wma) <= 0):
            return None
        return SqueezeSignal(direction, bar.time_close, self.atr)
