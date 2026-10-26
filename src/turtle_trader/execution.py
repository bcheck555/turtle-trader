"""In-memory broker contract and deterministic offline order ledger.

No external broker client, credentials, persistence, or recovery is implemented.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol

from .domain import ExecutionConfig, Fill, OrderIntent
from .portfolio import Portfolio, Position


@dataclass(frozen=True)
class Account:
    cash: Decimal
    equity: Decimal


@dataclass
class OrderState:
    intent: OrderIntent
    filled_quantity: int = 0
    status: str = "open"

    @property
    def remaining(self) -> int:
        return self.intent.quantity - self.filled_quantity


class Broker(Protocol):
    def get_account(self) -> Account: ...
    def get_positions(self) -> list[Position]: ...
    def get_open_orders(self) -> list[OrderState]: ...
    def get_order(self, order_id: str) -> OrderState: ...
    def submit_order(self, intent: OrderIntent) -> OrderState: ...
    def cancel_order(self, order_id: str) -> None: ...
    def reconcile(self) -> None: ...


class OfflineBroker:
    def __init__(self, portfolio: Portfolio, config: ExecutionConfig) -> None:
        self.portfolio, self.config = portfolio, config
        self.orders: dict[str, OrderState] = {}
        self.fills: list[Fill] = []

    def get_account(self) -> Account:
        return Account(self.portfolio.cash, self.portfolio.equity)

    def get_positions(self) -> list[Position]:
        return list(self.portfolio.positions.values())

    def get_open_orders(self) -> list[OrderState]:
        return [o for o in self.orders.values() if o.status == "open"]

    def get_order(self, order_id: str) -> OrderState:
        return self.orders[order_id]

    def submit_order(self, intent: OrderIntent) -> OrderState:
        if intent.id in self.orders or intent.quantity <= 0:
            raise ValueError("duplicate id or nonpositive quantity")
        order = OrderState(intent)
        self.orders[intent.id] = order
        return order

    def cancel_order(self, order_id: str) -> None:
        order = self.get_order(order_id)
        if order.status == "open":
            order.status = "cancelled"

    def reconcile(self) -> None:
        if any(p.quantity <= 0 for p in self.get_positions()):
            raise ValueError("invalid offline account state")

    def price(self, reference: Decimal, side: str) -> Decimal:
        adjustment = self.config.slippage_bps / 10000
        return reference * (1 + adjustment if side == "buy" else 1 - adjustment)

    def fill(self, order_id: str, quantity: int, price: Decimal, time: datetime, *,
             time_precision: Literal["bar_open", "intrabar_unknown"] = "bar_open",
             bar_end: datetime | None = None) -> Fill:
        order = self.get_order(order_id)
        if order.status != "open" or not 0 < quantity <= order.remaining:
            raise ValueError("invalid fill quantity or order status")
        if time < order.intent.eligible_at:
            raise ValueError("fill precedes eligibility")
        fee = self.config.fee_per_share * quantity
        if order.filled_quantity == 0:
            fee += self.config.fee_per_order
        fill = Fill(order_id, order.intent.symbol, order.intent.side,
                    quantity, price, fee, time, order.intent.reason, time_precision, bar_end,
                    direction=order.intent.direction, intent=order.intent.intent,
                    campaign_id=order.intent.campaign_id, unit_id=order.intent.unit_id)
        self.portfolio.apply(fill, order.intent.stop)
        order.filled_quantity += quantity
        if order.remaining == 0:
            order.status = "filled"
        self.fills.append(fill)
        return fill
