"""Versioned trading contracts; money is represented with Decimal."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from math import isfinite
from typing import Literal


def _positive(value: float, name: str) -> None:
    try:
        valid = type(value) in (int, float) and isfinite(value) and value > 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{name} must be finite and positive")


def _decimal(value: Decimal, name: str, *, positive: bool = False) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    if value < 0 or (positive and value == 0):
        raise ValueError(f"{name} must be {'positive' if positive else 'nonnegative'}")


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
    entry_strategy: str = "turtle_s2"
    exit_strategy: str = "turtle_channel"
    allow_short: bool = False
    entry_length: int = 0
    exit_length: int = 0
    failsafe_length: int = 55
    atr_length: int = 20
    atr_multiple: float = 2.0
    add_multiple: float = 0.5
    interval_minutes: int = 5
    tick_size: Decimal = Decimal("0.01")
    chandelier_timeframe: str = "session"
    chandelier_length: int = 22
    chandelier_atr_length: int = 22
    chandelier_multiple: float = 3.0
    chandelier_ratchet: bool = True
    squeeze_bb_length: int = 20
    squeeze_bb_multiple: float = 2.0
    squeeze_kc_length: int = 20
    squeeze_kc_multiple: float = 1.5
    squeeze_momentum_length: int = 20
    squeeze_adx_length: int = 14
    squeeze_adx_smoothing: int = 14
    squeeze_adx_threshold: float = 20.0
    squeeze_use_adx: bool = True
    squeeze_wma_length: int = 120
    squeeze_use_wma: bool = True
    squeeze_atr_length: int = 14
    squeeze_target_multiple: float = 3.0
    squeeze_flip_on_opposite: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.entry_strategy, str) or not self.entry_strategy:
            raise ValueError("entry_strategy must be a nonempty registered identifier")
        if not isinstance(self.exit_strategy, str) or not self.exit_strategy:
            raise ValueError("exit_strategy must be a nonempty registered identifier")
        if self.entry_length == 0 and type(self.entry_length) is int:
            object.__setattr__(self, "entry_length",
                               20 if self.entry_strategy in ("turtle_s1", "squeeze_momentum")
                               else 55)
        if self.exit_length == 0 and type(self.exit_length) is int:
            if self.entry_strategy not in ("turtle_s1", "turtle_s2", "squeeze_momentum"):
                raise ValueError("alternate entry strategies require explicit exit_length")
            object.__setattr__(self, "exit_length",
                               10 if self.entry_strategy == "turtle_s1" else 20)
        if (self.entry_strategy == "squeeze_momentum") != (self.exit_strategy == "atr_bracket"):
            raise ValueError("squeeze_momentum entry requires atr_bracket exit and vice versa")
        for name in ("entry_length", "exit_length", "failsafe_length", "atr_length",
                     "chandelier_length", "chandelier_atr_length", "squeeze_bb_length",
                     "squeeze_kc_length", "squeeze_momentum_length", "squeeze_adx_length",
                     "squeeze_adx_smoothing", "squeeze_wma_length", "squeeze_atr_length"):
            value = getattr(self, name)
            if type(value) is not int or not 2 <= value <= 10000:
                raise ValueError(f"{name} must be an integer in 2..10000")
        if type(self.interval_minutes) is not int or self.interval_minutes not in (
            1, 2, 3, 5, 6, 10, 15, 30,
        ):
            raise ValueError("interval_minutes must be a supported intraday interval")
        for name in ("atr_multiple", "add_multiple", "chandelier_multiple",
                     "squeeze_bb_multiple", "squeeze_kc_multiple", "squeeze_target_multiple"):
            _positive(getattr(self, name), name)
        try:
            valid_threshold = (type(self.squeeze_adx_threshold) in (int, float)
                               and isfinite(self.squeeze_adx_threshold)
                               and self.squeeze_adx_threshold >= 0)
        except OverflowError:
            valid_threshold = False
        if not valid_threshold:
            raise ValueError("squeeze_adx_threshold must be finite and nonnegative")
        _decimal(self.tick_size, "tick_size", positive=True)
        for name in ("allow_short", "chandelier_ratchet", "squeeze_use_adx",
                     "squeeze_use_wma", "squeeze_flip_on_opposite"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be boolean")
        if self.chandelier_timeframe not in ("session", "intraday"):
            raise ValueError("chandelier_timeframe must be session or intraday")


@dataclass(frozen=True)
class RiskConfig:
    starting_cash: Decimal = Decimal("25000")
    unit_fraction: Decimal = Decimal("0.01")
    max_units: int = 4
    close_limit: int = 6
    loose_limit: int = 10
    direction_limit: int = 12
    close_groups: tuple[tuple[str, ...], ...] = ()
    loose_groups: tuple[tuple[str, ...], ...] = ()
    position_sizing: Literal["atr", "available_cash"] = "atr"

    def __post_init__(self) -> None:
        if self.position_sizing not in ("atr", "available_cash"):
            raise ValueError("position_sizing must be atr or available_cash")
        _decimal(self.starting_cash, "starting_cash", positive=True)
        _decimal(self.unit_fraction, "unit_fraction", positive=True)
        if self.unit_fraction > 1:
            raise ValueError("unit_fraction must not exceed one")
        for name in ("max_units", "close_limit", "loose_limit", "direction_limit"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("close_groups", "loose_groups"):
            groups = getattr(self, name)
            if not isinstance(groups, tuple) or any(
                not isinstance(group, tuple) or not group
                or any(not isinstance(s, str) or not s.strip() for s in group)
                or len(group) != len(set(group)) for group in groups
            ):
                raise ValueError(f"{name} must contain nonempty unique-symbol groups")


@dataclass(frozen=True)
class ExecutionConfig:
    slippage_bps: Decimal = Decimal("5")
    fee_per_share: Decimal = Decimal("0")
    fee_per_order: Decimal = Decimal("0")
    participation_rate: Decimal = Decimal("1")
    market_hours: str = "regular"
    shortable_symbols: tuple[str, ...] = ()
    borrow_rate: Decimal | None = None
    initial_short_collateral: Decimal = Decimal("1.5")
    maintenance_short_collateral: Decimal = Decimal("1.3")

    def __post_init__(self) -> None:
        for name in ("slippage_bps", "fee_per_share", "fee_per_order"):
            _decimal(getattr(self, name), name)
        if self.slippage_bps >= 10000:
            raise ValueError("slippage must be below 10000 basis points")
        _decimal(self.participation_rate, "participation_rate", positive=True)
        if self.participation_rate > 1:
            raise ValueError("participation_rate must not exceed one")
        if self.market_hours != "regular":
            raise ValueError("only regular exchange sessions are supported")
        if self.borrow_rate is not None:
            _decimal(self.borrow_rate, "borrow_rate")
        for name in ("initial_short_collateral", "maintenance_short_collateral"):
            _decimal(getattr(self, name), name, positive=True)
            if getattr(self, name) <= 1:
                raise ValueError(f"{name} must exceed one")
        if self.initial_short_collateral < self.maintenance_short_collateral:
            raise ValueError("initial collateral must be at least maintenance collateral")
        if not isinstance(self.shortable_symbols, tuple) or any(
            not isinstance(s, str) or not s.strip() for s in self.shortable_symbols
        ) or len(set(self.shortable_symbols)) != len(self.shortable_symbols):
            raise ValueError("shortable_symbols must contain unique nonempty symbols")


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
    direction: int = 1
    intent: Literal["open", "reduce"] = "open"
    campaign_id: str = ""
    unit_id: str = ""


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
    direction: int = 1
    intent: Literal["open", "reduce"] = "open"
    campaign_id: str = ""
    unit_id: str = ""


@dataclass(frozen=True)
class RiskDecision:
    quantity: int
    reason: str
    planned_risk: Decimal = Decimal("0")
