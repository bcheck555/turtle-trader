"""Entry sizing and a New York calendar-day loss latch."""

from datetime import date, datetime
from decimal import ROUND_FLOOR, Decimal
from zoneinfo import ZoneInfo

from .domain import ExecutionConfig, RiskConfig, RiskDecision

NY = ZoneInfo("America/New_York")


def size_entry(*, equity: Decimal, cash: Decimal, price: Decimal, stop: Decimal,
               config: RiskConfig, execution: ExecutionConfig,
               occupied_positions: int = 0, existing_quantity: int = 0,
               existing_risk: Decimal = Decimal(0),
               daily_loss_latched: bool = False) -> RiskDecision:
    """Bound shares by loss budget, allocation, available cash, and slots.

    Price already includes entry slippage. Risk includes estimated exit slippage
    and both sides' fees; this is a planned bound, not a guarantee across gaps.
    """
    if daily_loss_latched:
        return RiskDecision(0, "daily_loss_latched")
    if price <= stop or stop <= 0:
        return RiskDecision(0, "opening_at_or_below_stop")
    if existing_quantity == 0 and occupied_positions >= config.max_positions:
        return RiskDecision(0, "max_positions")
    exit_price = stop * (1 - execution.slippage_bps / 10000)
    unit_risk = price - exit_price + 2 * execution.fee_per_share
    # Entry fees and slippage also reduce equity immediately. Solve against that
    # post-fill budget, rather than allowing an exactly sized order to exceed it.
    reference_price = price / (1 + execution.slippage_bps / 10000)
    entry_cost = price - reference_price + execution.fee_per_share
    risk_budget = (equity * config.risk_fraction - existing_risk
                   - (2 + config.risk_fraction) * execution.fee_per_order)
    allocation = equity * config.allocation_fraction - existing_quantity * price
    available_cash = cash - execution.fee_per_order
    limits = (
        risk_budget / (unit_risk + config.risk_fraction * entry_cost),
        allocation / price,
        available_cash / (price + execution.fee_per_share),
    )
    quantity = max(0, int(min(limits).to_integral_value(rounding=ROUND_FLOOR)))
    return RiskDecision(quantity, "accepted" if quantity else "insufficient_budget",
                        quantity * unit_risk + 2 * execution.fee_per_order
                        if quantity else Decimal(0))


class DailyLossGuard:
    """Reset before new-day price marks, using last known midnight equity."""

    def __init__(self, config: RiskConfig) -> None:
        self.config = config
        self.day: date | None = None
        self.baseline = config.starting_cash
        self.latched = False

    def roll_day(self, time: datetime, equity: Decimal) -> None:
        day = time.astimezone(NY).date()
        if day != self.day:
            self.day, self.baseline, self.latched = day, equity, False

    def observe(self, equity: Decimal) -> bool:
        if equity <= self.baseline * (1 - self.config.daily_loss_fraction):
            self.latched = True
        return self.latched
