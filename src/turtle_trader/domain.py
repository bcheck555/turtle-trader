"""Shared immutable input/output contracts; monetary values are Decimal."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class Bar:
    symbol: str
    time: datetime
    time_close: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class StrategyConfig:
    entry_length: int = 20
    exit_length: int = 10
    atr_length: int = 20
    atr_multiple: float = 2.0
    interval_minutes: int = 5
    day_trading: bool = True
    close_hour: int = 16
    close_minute: int = 30
    open_hour: int = 18
    open_minute: int = 0

    def __post_init__(self) -> None:
        if any(not 2 <= n <= 400 for n in (
            self.entry_length, self.exit_length, self.atr_length
        )):
            raise ValueError("indicator lengths must be 2..400")
        if self.interval_minutes not in (1, 2, 3, 5, 6, 10, 15, 30):
            raise ValueError("interval must be a minute divisor of 30")
        if not 0.5 <= self.atr_multiple < float("inf"):
            raise ValueError("ATR multiple must be finite and at least 0.5")
        if any(not 0 <= h <= 23 for h in (self.close_hour, self.open_hour)):
            raise ValueError("invalid schedule hour")
        if any(m not in range(0, 56, 5) for m in (self.close_minute, self.open_minute)):
            raise ValueError("schedule minutes must be 0..55 in steps of five")
        close = self.close_hour * 60 + self.close_minute
        opening = self.open_hour * 60 + self.open_minute
        if self.day_trading and (close >= opening or any(
            t % self.interval_minutes for t in (close, opening)
        )):
            raise ValueError("close must precede reentry and both must align to candles")


@dataclass(frozen=True)
class StrategyEvents:
    symbol: str
    time: datetime
    known_at: datetime
    entry_level: float | None = None
    exit_level: float | None = None
    atr: float | None = None
    initial_stop: float | None = None
    active_stop: float | None = None
    signal_long: bool = False
    held: bool = False
    entry: bool = False
    reentry: bool = False
    scheduled_close: bool = False
    signal_exit: bool = False
    entry_mark: float | None = None
    exit_mark: float | None = None
    signal_exit_price: float | None = None
    shade_held: bool = False
    deadline: datetime | None = None
    exit_while_flat: bool = False


@dataclass(frozen=True)
class RiskConfig:
    starting_cash: Decimal = Decimal("25000")
    risk_fraction: Decimal = Decimal("0.005")
    allocation_fraction: Decimal = Decimal("0.15")
    max_positions: int = 5
    daily_loss_fraction: Decimal = Decimal("0.02")

    def __post_init__(self) -> None:
        if not self.starting_cash.is_finite() or self.starting_cash <= 0:
            raise ValueError("starting cash must be finite and positive")
        for value in (self.risk_fraction, self.allocation_fraction, self.daily_loss_fraction):
            if not value.is_finite() or not 0 < value <= 1:
                raise ValueError("risk fractions must be finite in (0, 1]")
        if self.max_positions < 1:
            raise ValueError("max_positions must be positive")


@dataclass(frozen=True)
class ExecutionConfig:
    slippage_bps: Decimal = Decimal("5")
    fee_per_share: Decimal = Decimal("0")
    fee_per_order: Decimal = Decimal("0")
    participation_rate: Decimal = Decimal("1")
    market_hours: Literal["regular", "extended"] = "regular"

    def __post_init__(self) -> None:
        for v in (self.slippage_bps, self.fee_per_share, self.fee_per_order):
            if not v.is_finite() or v < 0:
                raise ValueError("costs must be finite and nonnegative")
        if self.slippage_bps >= 10000:
            raise ValueError("slippage must be less than 10000 bps")
        if not self.participation_rate.is_finite() or not 0 < self.participation_rate <= 1:
            raise ValueError("participation rate must be in (0, 1]")
        if self.market_hours not in ("regular", "extended"):
            raise ValueError("unknown market hours")


@dataclass(frozen=True)
class OrderIntent:
    id: str
    symbol: str
    side: Literal["buy", "sell"]
    quantity: int
    created_at: datetime
    eligible_at: datetime
    reason: str
    stop: Decimal | None = None


@dataclass(frozen=True)
class Fill:
    order_id: str
    symbol: str
    side: Literal["buy", "sell"]
    quantity: int
    price: Decimal
    fee: Decimal
    time: datetime
    reason: str
    time_precision: Literal["bar_open", "intrabar_unknown"] = "bar_open"
    bar_end: datetime | None = None


@dataclass(frozen=True)
class RiskDecision:
    quantity: int
    reason: str
    planned_risk: Decimal = Decimal("0")
