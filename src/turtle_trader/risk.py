"""Faith volatility units, portfolio caps, and drawdown sizing capital."""

from datetime import datetime
from decimal import Decimal

from .domain import RiskConfig


class SizingCapital:
    def __init__(self, starting_cash: Decimal) -> None:
        self.year: int | None = None
        self.baseline = self.capital = self.anchor = starting_cash

    def observe(self, time: datetime, equity: Decimal) -> None:
        if self.year != time.year:
            self.year = time.year
            self.baseline = self.capital = self.anchor = max(Decimal(0), equity)
        if equity <= 0:
            self.capital = Decimal(0)
            return
        if equity >= self.baseline:
            self.capital = self.baseline
            self.anchor = self.baseline
        # Successive loss thresholds form a geometric series with finite sum.
        # Beyond that limit no positive sizing capital can satisfy the rule.
        if equity <= self.anchor - self.capital / 2:
            self.capital = Decimal(0)
            self.anchor = equity
            return
        while self.capital > 0 and equity <= self.anchor - self.capital * Decimal("0.1"):
            self.anchor -= self.capital * Decimal("0.1")
            self.capital *= Decimal("0.8")


def unit_shares(capital: Decimal, n: Decimal, config: RiskConfig) -> int:
    return max(0, int(capital * config.unit_fraction / n)) if n > 0 else 0


def validate_groups(config: RiskConfig, symbols: set[str]) -> None:
    for groups in (config.close_groups, config.loose_groups):
        if groups and set().union(*(set(g) for g in groups)) != symbols:
            raise ValueError("correlation groups must cover exactly the dataset symbols")


def unit_limit_reason(config: RiskConfig, symbol: str, direction: int,
                      occupied: list[tuple[str, int]]) -> str | None:
    same = [s for s, d in occupied if d == direction]
    if same.count(symbol) >= config.max_units:
        return "symbol_unit_limit"
    if len(same) >= config.direction_limit:
        return "direction_unit_limit"
    for groups, limit, reason in (
        (config.close_groups, config.close_limit, "close_correlation_limit"),
        (config.loose_groups, config.loose_limit, "loose_correlation_limit"),
    ):
        relevant = [g for g in groups if symbol in g] if groups else [tuple(same + [symbol])]
        if any(sum(s in group for s in same) >= limit for group in relevant):
            return reason
    return None
