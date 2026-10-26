"""Signed equity ledger with individual units and segregated short collateral."""

from dataclasses import dataclass
from decimal import Decimal

from .domain import Fill


@dataclass
class Position:
    symbol: str
    quantity: int
    average_price: Decimal
    stop: Decimal | None
    direction: int = 1
    campaign_id: str = ""
    unit_id: str = ""
    collateral: Decimal = Decimal(0)


class Portfolio:
    def __init__(self, cash: Decimal, short_collateral: Decimal = Decimal("1.5")) -> None:
        self.cash = cash
        self.short_collateral = short_collateral
        self.lots: dict[str, Position] = {}
        self.marks: dict[str, Decimal] = {}
        self.realized_pnl = Decimal(0)
        self.fees = Decimal(0)
        self.borrow_costs = Decimal(0)

    @property
    def positions(self) -> dict[str, Position]:
        combined: dict[str, Position] = {}
        for lot in self.lots.values():
            if lot.symbol not in combined:
                combined[lot.symbol] = Position(
                    lot.symbol, lot.quantity, lot.average_price, lot.stop, lot.direction,
                    lot.campaign_id, collateral=lot.collateral)
            else:
                position = combined[lot.symbol]
                total = position.quantity + lot.quantity
                position.average_price = (
                    position.average_price * position.quantity
                    + lot.average_price * lot.quantity) / total
                position.quantity = total
                position.collateral += lot.collateral
                if lot.stop is not None and position.stop is not None:
                    position.stop = (max if lot.direction == 1 else min)(position.stop, lot.stop)
        return combined

    @property
    def exposure(self) -> Decimal:
        return sum((p.quantity * self.marks.get(p.symbol, p.average_price)
                    for p in self.lots.values()), Decimal(0))

    @property
    def net_exposure(self) -> Decimal:
        return sum((p.direction * p.quantity * self.marks.get(p.symbol, p.average_price)
                    for p in self.lots.values()), Decimal(0))

    @property
    def short_liability(self) -> Decimal:
        return sum((p.quantity * self.marks.get(p.symbol, p.average_price)
                    for p in self.lots.values() if p.direction == -1), Decimal(0))

    @property
    def collateral(self) -> Decimal:
        return sum((p.collateral for p in self.lots.values()), Decimal(0))

    @property
    def available_cash(self) -> Decimal:
        return self.cash - self.collateral

    @property
    def equity(self) -> Decimal:
        return self.cash + self.net_exposure

    def apply(self, fill: Fill, stop: Decimal | None = None) -> None:
        if fill.quantity <= 0 or fill.price <= 0 or fill.fee < 0:
            raise ValueError("invalid fill")
        if fill.direction not in (-1, 1) or fill.intent not in ("open", "reduce"):
            raise ValueError("invalid fill direction or intent")
        expected = "buy" if (fill.direction == 1) == (fill.intent == "open") else "sell"
        if fill.side != expected:
            raise ValueError("fill side conflicts with direction/intent")
        key = fill.unit_id or fill.symbol
        lot = self.lots.get(key)
        value = fill.price * fill.quantity
        if fill.intent == "open":
            if lot and (lot.direction != fill.direction or lot.symbol != fill.symbol):
                raise ValueError("unit direction/symbol mismatch")
            if any(p.symbol == fill.symbol and p.direction != fill.direction
                   for p in self.lots.values()):
                raise ValueError("opposing campaigns cannot overlap")
            required = value if fill.direction == 1 else value * (self.short_collateral - 1)
            if required + fill.fee > self.available_cash:
                raise ValueError("insufficient unencumbered cash")
            self.cash -= fill.direction * value + fill.fee
            collateral = value * self.short_collateral if fill.direction == -1 else Decimal(0)
            if lot is None:
                self.lots[key] = Position(fill.symbol, fill.quantity, fill.price, stop,
                                         fill.direction, fill.campaign_id, key, collateral)
            else:
                total = lot.quantity + fill.quantity
                lot.average_price = (lot.average_price * lot.quantity + value) / total
                lot.quantity = total
                lot.collateral += collateral
        else:
            if lot is None or lot.direction != fill.direction or fill.quantity > lot.quantity:
                raise ValueError("reduction exceeds unit holding")
            self.cash += fill.direction * value - fill.fee
            self.realized_pnl += fill.direction * (fill.price - lot.average_price) * fill.quantity
            lot.collateral *= Decimal(lot.quantity - fill.quantity) / lot.quantity
            lot.quantity -= fill.quantity
            if lot.quantity == 0:
                del self.lots[key]
        self.fees += fill.fee
