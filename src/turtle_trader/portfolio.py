"""Long-only cash ledger. Decimal arithmetic is retained through every fill."""

from dataclasses import dataclass
from decimal import Decimal

from .domain import Fill


@dataclass
class Position:
    symbol: str
    quantity: int
    average_price: Decimal
    stop: Decimal | None


class Portfolio:
    def __init__(self, cash: Decimal) -> None:
        self.cash = cash
        self.positions: dict[str, Position] = {}
        self.marks: dict[str, Decimal] = {}
        self.realized_pnl = Decimal(0)
        self.fees = Decimal(0)

    @property
    def exposure(self) -> Decimal:
        return sum((p.quantity * self.marks.get(s, p.average_price)
                    for s, p in self.positions.items()), Decimal(0))

    @property
    def equity(self) -> Decimal:
        return self.cash + self.exposure

    def apply(self, fill: Fill, stop: Decimal | None = None) -> None:
        if fill.quantity <= 0 or fill.price <= 0 or fill.fee < 0:
            raise ValueError("invalid fill")
        position = self.positions.get(fill.symbol)
        notional = fill.price * fill.quantity
        if fill.side == "buy":
            if notional + fill.fee > self.cash:
                raise ValueError("fill would use margin")
            self.cash -= notional + fill.fee
            if position is None:
                self.positions[fill.symbol] = Position(
                    fill.symbol, fill.quantity, fill.price, stop)
            else:
                quantity = position.quantity + fill.quantity
                position.average_price = (
                    position.average_price * position.quantity + notional) / quantity
                position.quantity = quantity
        else:
            if position is None or fill.quantity > position.quantity:
                raise ValueError("fill would sell short")
            self.cash += notional - fill.fee
            self.realized_pnl += (fill.price - position.average_price) * fill.quantity
            position.quantity -= fill.quantity
            if position.quantity == 0:
                del self.positions[fill.symbol]
        self.fees += fill.fee
