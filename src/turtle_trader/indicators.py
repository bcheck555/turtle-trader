"""Streaming true range and Wilder smoothing used for Turtle N."""

from dataclasses import dataclass


def true_range(high: float, low: float, previous_close: float | None) -> float:
    if previous_close is None:
        return high - low
    return max(high - low, abs(high - previous_close), abs(low - previous_close))


@dataclass
class WilderATR:
    length: int
    count: int = 0
    seed: float = 0.0
    value: float | None = None

    def update(self, value: float) -> float | None:
        if self.value is None:
            self.seed += value
            self.count += 1
            if self.count == self.length:
                self.value = self.seed / self.length
        else:
            self.value = (self.value * (self.length - 1) + value) / self.length
        return self.value
