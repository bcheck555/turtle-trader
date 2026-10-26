"""Causal bar-close decisions with next-eligible-open execution.

OHLC cannot determine intrabar paths. Market orders fill at the open; confirmed
positions then receive their previously known protective stop, including on the
entry bar. Stops use strict low < stop and adverse opening gaps. A shared bar
volume cap applies to all fills; triggered, unfilled exits remain market orders.
That cap uses completed candle volume as a simulation scenario; available
liquidity at the open cannot be inferred from OHLCV. Intrabar fill timestamps
identify the candle, not a reconstructed tick time.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import groupby
from math import isfinite
from typing import Protocol

from .domain import (
    Bar,
    ExecutionConfig,
    Fill,
    OrderIntent,
    RiskConfig,
    RiskDecision,
    StrategyConfig,
    StrategyEvents,
)
from .execution import OfflineBroker, OrderState
from .portfolio import Portfolio, Position
from .research import PriceBasis, research_warnings, validate_price_basis
from .risk import DailyLossGuard, size_entry
from .strategy import Strategy


class Calendar(Protocol):
    def eligible(self, at: datetime, market_hours: str = "regular") -> bool: ...


@dataclass(frozen=True)
class EquitySnapshot:
    time: datetime
    cash: Decimal
    equity: Decimal
    exposure: Decimal
    daily_loss_latched: bool


@dataclass(frozen=True)
class RiskRecord:
    time: datetime
    symbol: str
    quantity: int
    reason: str
    planned_risk: Decimal


@dataclass(frozen=True)
class Rejection:
    time: datetime
    symbol: str
    reason: str
    order_id: str | None = None


@dataclass(frozen=True)
class Cancellation:
    time: datetime
    symbol: str
    order_id: str
    reason: str
    remaining_quantity: int


@dataclass(frozen=True)
class DeadlineExit:
    symbol: str
    deadline: datetime
    fill_time: datetime
    lateness_seconds: float


@dataclass(frozen=True)
class ExecutionAmbiguity:
    time: datetime
    symbol: str
    reason: str


@dataclass
class BacktestResult:
    events: list[StrategyEvents] = field(default_factory=list)
    orders: list[OrderIntent] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    equity: list[EquitySnapshot] = field(default_factory=list)
    risk_decisions: list[RiskRecord] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    cancellations: list[Cancellation] = field(default_factory=list)
    deadline_exits: list[DeadlineExit] = field(default_factory=list)
    execution_ambiguities: list[ExecutionAmbiguity] = field(default_factory=list)
    positions: list[Position] = field(default_factory=list)
    order_states: list[OrderState] = field(default_factory=list)
    metrics: dict[str, Decimal | int | float | str] = field(default_factory=dict)
    price_basis: PriceBasis = "action_free"
    research_warnings: list[str] = field(default_factory=list)


def decimal(value: float) -> Decimal:
    return Decimal(str(value))


class _Replay:
    def __init__(self, strategy_config: StrategyConfig, risk_config: RiskConfig,
                 execution_config: ExecutionConfig, calendar: Calendar,
                 trade_start: datetime | None = None,
                 trade_end: datetime | None = None) -> None:
        self.strategy_config, self.risk_config = strategy_config, risk_config
        self.config, self.calendar = execution_config, calendar
        self.portfolio = Portfolio(risk_config.starting_cash)
        self.broker = OfflineBroker(self.portfolio, execution_config)
        self.guard = DailyLossGuard(risk_config)
        self.strategies: dict[str, Strategy] = {}
        self.deadlines: dict[str, datetime] = {}
        self.active_stops: dict[str, Decimal] = {}
        self.reservation_prices: dict[str, Decimal] = {}
        self.result = BacktestResult()
        self.unexecutable = 0
        self.trade_start, self.trade_end = trade_start, trade_end

    def trading_at(self, time: datetime) -> bool:
        return ((self.trade_start is None or time >= self.trade_start)
                and (self.trade_end is None or time < self.trade_end))

    def pending(self, symbol: str, side: str) -> list[OrderState]:
        return [o for o in self.broker.get_open_orders()
                if o.intent.symbol == symbol and o.intent.side == side]

    def cancel(self, order: OrderState, time: datetime, reason: str) -> None:
        self.result.cancellations.append(Cancellation(
            time, order.intent.symbol, order.intent.id, reason, order.remaining))
        self.broker.cancel_order(order.intent.id)

    def cancel_buys(self, symbol: str, time: datetime, reason: str) -> None:
        for order in self.pending(symbol, "buy"):
            self.cancel(order, time, reason)

    def submit(self, symbol: str, side: str, quantity: int, created: datetime,
               eligible: datetime, reason: str, stop: Decimal | None = None) -> OrderState:
        intent = OrderIntent(f"order-{len(self.result.orders) + 1:06d}", symbol,
                             "buy" if side == "buy" else "sell", quantity,
                             created, eligible, reason, stop)
        self.result.orders.append(intent)
        return self.broker.submit_order(intent)

    def exit(self, symbol: str, created: datetime, eligible: datetime, reason: str) -> None:
        self.cancel_buys(symbol, created, reason)
        position = self.portfolio.positions.get(symbol)
        if position is not None and not self.pending(symbol, "sell"):
            self.submit(symbol, "sell", position.quantity, created, eligible, reason)

    def reserved_cash(self, excluding: str | None = None) -> Decimal:
        total = Decimal(0)
        for order in self.broker.get_open_orders():
            if order.intent.side == "buy" and order.intent.id != excluding:
                price = self.reservation_prices[order.intent.id]
                total += order.remaining * (price + self.config.fee_per_share)
                if not order.filled_quantity:
                    total += self.config.fee_per_order
        return total

    def decide(self, symbol: str, time: datetime, price: Decimal, stop: Decimal,
               excluding: str | None = None) -> RiskDecision:
        occupied = set(self.portfolio.positions)
        occupied.update(o.intent.symbol for o in self.broker.get_open_orders()
                        if o.intent.side == "buy" and o.intent.id != excluding)
        occupied.discard(symbol)
        position = self.portfolio.positions.get(symbol)
        existing_quantity = position.quantity if position else 0
        estimated_exit = stop * (1 - self.config.slippage_bps / 10000)
        existing_risk = (
            max(Decimal(0), position.average_price - estimated_exit
                + 2 * self.config.fee_per_share) * existing_quantity
            if position else Decimal(0))
        decision = size_entry(
            equity=self.portfolio.equity,
            cash=max(Decimal(0), self.portfolio.cash - self.reserved_cash(excluding)),
            price=price, stop=stop, config=self.risk_config, execution=self.config,
            occupied_positions=len(occupied), existing_quantity=existing_quantity,
            existing_risk=existing_risk, daily_loss_latched=self.guard.latched)
        self.result.risk_decisions.append(RiskRecord(
            time, symbol, decision.quantity, decision.reason, decision.planned_risk))
        return decision

    def observe_loss(self, time: datetime) -> None:
        if self.guard.observe(self.portfolio.equity):
            for order in list(self.broker.get_open_orders()):
                if order.intent.side == "buy":
                    self.cancel(order, time, "daily_loss_latched")

    def fill_order(self, order: OrderState, bar: Bar, capacity: dict[str, int],
                   reference: Decimal | None = None) -> None:
        symbol = bar.symbol
        if order.status != "open" or bar.time < order.intent.eligible_at:
            return
        price = self.broker.price(reference if reference is not None else decimal(bar.open),
                                  order.intent.side)
        quantity = min(order.remaining, capacity[symbol])
        if order.intent.side == "buy":
            stop = order.intent.stop
            if stop is None:
                raise ValueError("entry order must have a source stop")
            protection = self.active_stops.get(symbol, stop)
            if decimal(bar.open) <= max(stop, protection):
                self.result.rejections.append(Rejection(
                    bar.time, symbol, "opening_at_or_below_stop", order.intent.id))
                self.cancel(order, bar.time, "opening_at_or_below_stop")
                return
            decision = self.decide(symbol, bar.time, price, stop, order.intent.id)
            quantity = min(quantity, decision.quantity)
            if decision.quantity == 0:
                self.result.rejections.append(Rejection(
                    bar.time, symbol, decision.reason, order.intent.id))
                self.cancel(order, bar.time, decision.reason)
                return
        else:
            position = self.portfolio.positions.get(symbol)
            quantity = min(quantity, position.quantity if position else 0)
        if quantity <= 0:
            return
        self.broker.fill(order.intent.id, quantity, price, bar.time,
                         time_precision="intrabar_unknown" if reference is not None else "bar_open",
                         bar_end=bar.time_close if reference is not None else None)
        if order.intent.side == "buy":
            self.portfolio.positions[symbol].stop = self.active_stops.get(symbol, order.intent.stop)
        capacity[symbol] -= quantity
        if order.intent.reason == "scheduled_deadline":
            deadline = self.deadlines[symbol]
            self.result.deadline_exits.append(DeadlineExit(
                symbol, deadline, bar.time, (bar.time - deadline).total_seconds()))
        self.observe_loss(bar.time)

    def open_batch(self, bars: list[Bar]) -> None:
        time = bars[0].time
        # A missing midnight bar uses the last known marks, then accounts for gaps.
        self.guard.roll_day(time, self.portfolio.equity)
        for bar in bars:
            self.portfolio.marks[bar.symbol] = decimal(bar.open)
        self.observe_loss(time)
        eligible = [b for b in bars if self.trading_at(b.time)
                    and self.calendar.eligible(b.time, self.config.market_hours)]
        capacity = {b.symbol: int(Decimal(b.volume) * self.config.participation_rate)
                    for b in eligible}
        for bar in eligible:
            deadline = self.deadlines.get(bar.symbol)
            if deadline is not None and bar.time >= deadline:
                self.exit(bar.symbol, deadline, deadline, "scheduled_deadline")
            position = self.portfolio.positions.get(bar.symbol)
            if position and position.stop is not None and decimal(bar.open) < position.stop:
                self.exit(bar.symbol, time, time, "protective_stop")
        # Sell first, then buy: independent of input ordering at shared timestamps.
        fills_before_open = len(self.broker.fills)
        for side in ("sell", "buy"):
            for bar in eligible:
                for order in self.pending(bar.symbol, side):
                    self.fill_order(order, bar, capacity)
        entered = {f.symbol for f in self.broker.fills[fills_before_open:] if f.side == "buy"}
        for bar in eligible:
            position = self.portfolio.positions.get(bar.symbol)
            if position and position.stop is not None and decimal(bar.low) < position.stop:
                if bar.symbol in entered:
                    self.result.execution_ambiguities.append(ExecutionAmbiguity(
                        bar.time, bar.symbol, "entry_bar_stop_adverse_path_assumed"))
                stop = position.stop
                self.exit(bar.symbol, time, time, "protective_stop")
                for order in self.pending(bar.symbol, "sell"):
                    self.fill_order(order, bar, capacity, min(decimal(bar.open), stop))

    def close_batch(self, bars: list[Bar]) -> None:
        for bar in bars:
            self.portfolio.marks[bar.symbol] = decimal(bar.close)
        # All current-close marks are applied together before allocating capital.
        time = max(b.time_close for b in bars)
        self.observe_loss(time)
        for bar in bars:
            strategy = self.strategies.get(bar.symbol)
            if strategy is None:
                strategy = Strategy(self.strategy_config)
                self.strategies[bar.symbol] = strategy
            event = strategy.on_bar(bar)
            self.result.events.append(event)
            if not self.trading_at(event.known_at):
                continue
            if event.held and event.active_stop is not None:
                self.active_stops[bar.symbol] = decimal(event.active_stop)
            if event.deadline is not None and (event.entry or event.reentry):
                self.deadlines[bar.symbol] = event.deadline
            if (event.entry or event.reentry) and not event.held:
                self.unexecutable += 1
                self.result.rejections.append(Rejection(
                    event.known_at, bar.symbol, "reference_round_trip_unexecutable"))
            if not event.held:
                self.exit(bar.symbol, event.known_at, event.known_at, "strategy_exit")
            else:
                position = self.portfolio.positions.get(bar.symbol)
                if position and event.active_stop is not None:
                    # Source band can decrease; do not convert it into a ratchet.
                    position.stop = decimal(event.active_stop)
                if ((event.entry or event.reentry) and position is None
                        and not self.pending(bar.symbol, "buy")):
                    if event.initial_stop is None:
                        self.result.rejections.append(Rejection(
                            event.known_at, bar.symbol, "missing_source_stop"))
                        continue
                    stop = decimal(event.initial_stop)
                    price = self.broker.price(decimal(bar.close), "buy")
                    decision = self.decide(bar.symbol, event.known_at, price, stop)
                    if decision.quantity:
                        order = self.submit(bar.symbol, "buy", decision.quantity,
                                            event.known_at, event.known_at, "strategy_entry", stop)
                        self.reservation_prices[order.intent.id] = price
                    else:
                        self.result.rejections.append(Rejection(
                            event.known_at, bar.symbol, decision.reason))
        self.broker.reconcile()
        if self.trade_start is None or time >= self.trade_start:
            self.result.equity.append(EquitySnapshot(
                time, self.portfolio.cash, self.portfolio.equity, self.portfolio.exposure,
                self.guard.latched))

    def finish(self) -> BacktestResult:
        self.result.fills = list(self.broker.fills)
        self.result.positions = [replace(p) for p in self.broker.get_positions()]
        self.result.order_states = list(self.broker.orders.values())
        peak = self.risk_config.starting_cash
        drawdown = Decimal(0)
        for snapshot in self.result.equity:
            peak = max(peak, snapshot.equity)
            drawdown = max(drawdown, (peak - snapshot.equity) / peak)
        self.result.metrics = {
            "starting_cash": self.risk_config.starting_cash,
            "ending_cash": self.portfolio.cash,
            "ending_equity": self.portfolio.equity,
            "net_pnl": self.portfolio.equity - self.risk_config.starting_cash,
            "realized_pnl_before_fees": self.portfolio.realized_pnl,
            "realized_pnl_after_fees": self.portfolio.realized_pnl - self.portfolio.fees,
            "fees": self.portfolio.fees,
            "total_return": self.portfolio.equity / self.risk_config.starting_cash - 1,
            "max_drawdown": drawdown,
            "fills": len(self.result.fills),
            "reference_round_trips_unexecutable": self.unexecutable,
            "open_positions": len(self.result.positions),
            "pending_orders": len(self.broker.get_open_orders()),
            "entry_bar_stop_ambiguities": len(self.result.execution_ambiguities),
        }
        return self.result


def run_backtest(bars: list[Bar], strategy_config: StrategyConfig,
                 risk_config: RiskConfig, execution_config: ExecutionConfig,
                 calendar: Calendar | None = None, *,
                 price_basis: PriceBasis = "action_free",
                 trade_start: datetime | None = None,
                 trade_end: datetime | None = None) -> BacktestResult:
    """Replay chronological, nonoverlapping per-symbol bars without liquidation.

    Warmup bars update reference state; new exposure waits for the next entry or
    reentry event within the trading window. End positions remain marked, without
    liquidation. Cross-symbol candles must share nonoverlapping boundaries.
    """
    price_basis = validate_price_basis(price_basis)
    for boundary in (trade_start, trade_end):
        if boundary is not None and boundary.tzinfo is None:
            raise ValueError("trade boundaries must be timezone-aware")
    if trade_start is not None and trade_end is not None and trade_start >= trade_end:
        raise ValueError("trade_start must precede trade_end")
    ordered = sorted((b for b in bars if trade_end is None or b.time_close <= trade_end),
                     key=lambda b: (b.time, b.symbol))
    previous: dict[str, datetime] = {}
    for bar in ordered:
        if bar.time.utcoffset() is None or bar.time_close.utcoffset() is None:
            raise ValueError("bar timestamps must be timezone-aware")
        prices = (bar.open, bar.high, bar.low, bar.close)
        if (not bar.symbol or not all(isfinite(p) and p > 0 for p in prices)
                or not isinstance(bar.volume, int) or bar.volume < 0
                or bar.low > min(bar.open, bar.close)
                or bar.high < max(bar.open, bar.close) or bar.low > bar.high):
            raise ValueError("invalid OHLCV bar")
        if bar.time >= bar.time_close or bar.time < previous.get(bar.symbol, bar.time):
            raise ValueError("duplicate or overlapping symbol bars")
        if (bar.time_close - bar.time != timedelta(minutes=strategy_config.interval_minutes)
                or bar.time.second or bar.time.microsecond
                or bar.time.minute % strategy_config.interval_minutes):
            raise ValueError("bar duration and alignment must match configured interval")
        previous[bar.symbol] = bar.time_close
    if calendar is None:
        from .data import ExchangeCalendar

        calendar = ExchangeCalendar(
            start=min(b.time.date() for b in ordered) if ordered else None,
            end=max(b.time_close.date() for b in ordered) if ordered else None)
    replay = _Replay(strategy_config, risk_config, execution_config, calendar,
                     trade_start, trade_end)
    previous_close: datetime | None = None
    for _, group in groupby(ordered, key=lambda b: b.time):
        batch = list(group)
        closes = {b.time_close for b in batch}
        if len(closes) != 1 or (previous_close is not None and batch[0].time < previous_close):
            raise ValueError("cross-symbol candle boundaries must align without overlap")
        previous_close = batch[0].time_close
        replay.open_batch(batch)
        replay.close_batch(batch)
    result = replay.finish()
    result.price_basis = price_basis
    result.research_warnings = research_warnings(price_basis)
    return result
