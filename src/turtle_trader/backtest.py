"""Prospective Turtle orders with conservative OHLC paths and isolated unit ledgers.

Completed-session observations publish future levels. Each candle is simulated
along both extremum orderings; only the lower closing-equity state is committed.
OHLC volume is a simulation capacity, never a claim about tick liquidity.
"""

from copy import copy, deepcopy
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from itertools import groupby
from math import isfinite
from typing import Literal, Protocol

from .domain import Bar, ExecutionConfig, Fill, OrderIntent, RiskConfig, StrategyConfig
from .execution import OfflineBroker, OrderState
from .halts import MARKET_HALTS, POLICY_VERSION, fully_halted, next_tradable_at
from .portfolio import Portfolio, Position
from .research import PriceBasis, research_warnings, strategy_warnings, validate_price_basis
from .risk import SizingCapital, unit_limit_reason, unit_shares, validate_groups
from .sessions import local_time
from .strategy import Strategy


class Calendar(Protocol):
    def eligible(self, at: datetime, market_hours: str = "regular") -> bool: ...


@dataclass(frozen=True)
class EquitySnapshot:
    time: datetime
    cash: Decimal
    equity: Decimal
    exposure: Decimal
    net_exposure: Decimal = Decimal(0)
    short_liability: Decimal = Decimal(0)
    collateral: Decimal = Decimal(0)
    borrow_costs: Decimal = Decimal(0)
    sizing_capital: Decimal = Decimal(0)


@dataclass(frozen=True)
class EngineEvent:
    symbol: str
    time: datetime
    kind: str
    direction: int = 0
    price: Decimal | None = None
    n: Decimal | None = None
    campaign_id: str = ""
    unit_id: str = ""
    detail: str = ""
    known_at: datetime | None = None


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
class ExecutionAmbiguity:
    time: datetime
    symbol: str
    reason: str


@dataclass
class BacktestResult:
    schema_version: int = 2
    events: list[EngineEvent] = field(default_factory=list)
    orders: list[OrderIntent] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    equity: list[EquitySnapshot] = field(default_factory=list)
    risk_decisions: list[RiskRecord] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    cancellations: list[Cancellation] = field(default_factory=list)
    execution_ambiguities: list[ExecutionAmbiguity] = field(default_factory=list)
    positions: list[Position] = field(default_factory=list)
    order_states: list[OrderState] = field(default_factory=list)
    metrics: dict[str, Decimal | int | float | str] = field(default_factory=dict)
    price_basis: PriceBasis = "action_free"
    research_warnings: list[str] = field(default_factory=list)
    exit_strategy: str = "turtle_channel"
    strategy_settings: dict[str, object] = field(default_factory=dict)
    strategy_warnings: list[str] = field(default_factory=list)


@dataclass
class Campaign:
    id: str
    symbol: str
    direction: int
    n: Decimal
    shares: int
    units: int = 0
    last_fill: Decimal | None = None
    closing: bool = False
    blocked_additions: bool = False


def decimal(value: float) -> Decimal:
    return Decimal(str(value))


class _Replay:
    def __init__(self, strategy: StrategyConfig, risk: RiskConfig,
                 execution: ExecutionConfig, symbols: set[str],
                 trade_start: datetime | None, trade_end: datetime | None) -> None:
        self.strategy_config, self.risk_config, self.config = strategy, risk, execution
        self.portfolio = Portfolio(risk.starting_cash, execution.initial_short_collateral)
        self.broker = OfflineBroker(self.portfolio, execution)
        self.strategies = {s: Strategy(strategy) for s in sorted(symbols)}
        self.campaigns: dict[str, Campaign] = {}
        self.weekly_shares: dict[str, int] = {}
        self.weeks: dict[str, tuple[int, int]] = {}
        self.sizing = SizingCapital(risk.starting_cash)
        self.borrow_day: date | None = None
        self.halted = False
        self.order_counter = self.campaign_counter = 0
        self.result = BacktestResult()
        self.trade_start, self.trade_end = trade_start, trade_end
        self.market_halts = False

    @property
    def squeeze(self) -> bool:
        return self.strategy_config.entry_strategy == "squeeze_momentum"

    def trading_at(self, at: datetime) -> bool:
        return ((self.trade_start is None or at >= self.trade_start)
                and (self.trade_end is None or at < self.trade_end))

    def fork(self) -> "_Replay":
        candidate = copy(self)
        candidate.portfolio = deepcopy(self.portfolio)
        candidate.broker = OfflineBroker(candidate.portfolio, self.config)
        candidate.broker.orders = deepcopy(self.broker.orders)
        candidate.strategies = deepcopy(self.strategies)
        candidate.campaigns = deepcopy(self.campaigns)
        candidate.sizing = deepcopy(self.sizing)
        candidate.weeks = self.weeks.copy()
        candidate.weekly_shares = self.weekly_shares.copy()
        candidate.result = BacktestResult()
        return candidate

    def commit(self, chosen: "_Replay") -> None:
        accumulated = self.result
        for name in ("events", "orders", "fills", "risk_decisions", "rejections",
                     "cancellations", "execution_ambiguities"):
            getattr(accumulated, name).extend(getattr(chosen.result, name))
        self.__dict__.update(chosen.__dict__)
        self.result = accumulated

    def cancel(self, order: OrderState, at: datetime, reason: str) -> None:
        if order.status == "open":
            self.result.cancellations.append(Cancellation(
                at, order.intent.symbol, order.intent.id, reason, order.remaining))
            self.broker.cancel_order(order.intent.id)

    def pending(self, symbol: str, intent: str) -> list[OrderState]:
        return [o for o in self.broker.get_open_orders()
                if o.intent.symbol == symbol and o.intent.intent == intent]

    def held_units(self, symbol: str) -> int:
        return sum(p.symbol == symbol for p in self.portfolio.lots.values())

    def reserved_cash(self, excluding: str = "") -> Decimal:
        total = Decimal(0)
        for order in self.broker.get_open_orders():
            if order.intent.intent != "open" or order.intent.id == excluding:
                continue
            campaign = self.campaigns[order.intent.symbol]
            base = order.intent.stop
            assert base is not None
            price = (base + campaign.direction * decimal(self.strategy_config.atr_multiple)
                     * campaign.n)
            factor = (Decimal(1) if campaign.direction == 1
                      else self.config.initial_short_collateral - 1)
            total += order.remaining * (price * factor + self.config.fee_per_share)
            if order.filled_quantity == 0:
                total += self.config.fee_per_order
        return total

    def submit(self, campaign: Campaign, quantity: int, bar: Bar, reason: str,
               intent: Literal["open", "reduce"], unit_id: str,
               stop: Decimal | None = None) -> OrderState:
        self.order_counter += 1
        side: Literal["buy", "sell"] = (
            "buy" if (campaign.direction == 1) == (intent == "open") else "sell")
        order = OrderIntent(f"order-{self.order_counter:06d}", campaign.symbol, side,
                            quantity, bar.time, bar.time, reason, stop,
                            direction=campaign.direction, intent=intent,
                            campaign_id=campaign.id, unit_id=unit_id)
        self.result.orders.append(order)
        return self.broker.submit_order(order)

    def fill(self, order: OrderState, reference: Decimal, bar: Bar, capacity: int,
             opening: bool) -> int:
        quantity = min(order.remaining, capacity)
        if quantity <= 0 or order.status != "open":
            return capacity
        campaign = self.campaigns[bar.symbol]
        price = self.broker.price(reference, order.intent.side)
        if order.intent.intent == "open":
            remaining_fee = self.config.fee_per_order if not order.filled_quantity else Decimal(0)
            factor = (Decimal(1) if campaign.direction == 1
                      else self.config.initial_short_collateral - 1)
            # Recheck the WHOLE outstanding unit after gaps; never silently resize it.
            required = order.remaining * (price * factor + self.config.fee_per_share)
            if required + remaining_fee > (
                self.portfolio.available_cash - self.reserved_cash(order.intent.id)
            ):
                self.cancel(order, bar.time, "insufficient_cash")
                if order.filled_quantity:
                    campaign.blocked_additions = True
                self.result.rejections.append(Rejection(
                    bar.time, bar.symbol, "insufficient_cash", order.intent.id))
                return capacity
        first_fill = order.filled_quantity == 0
        fill = self.broker.fill(order.intent.id, quantity, price, bar.time,
                                time_precision="bar_open" if opening else "intrabar_unknown",
                                bar_end=None if opening else bar.time_close)
        self.result.fills.append(fill)
        lot = self.portfolio.lots.get(order.intent.unit_id)
        if order.intent.intent == "open" and lot:
            distance = decimal(self.strategy_config.atr_multiple) * campaign.n
            if not self.squeeze or first_fill:
                lot.stop = (price if self.squeeze else lot.average_price) - (
                    campaign.direction * distance)
            if first_fill:
                for other in self.portfolio.lots.values():
                    if (other.campaign_id == campaign.id and other.unit_id != lot.unit_id
                            and other.stop is not None):
                        other.stop += (campaign.direction
                                       * decimal(self.strategy_config.add_multiple) * campaign.n)
                self.strategies[bar.symbol].on_fill(campaign.direction, float(price),
                                                     float(campaign.n))
            if order.status == "filled":
                campaign.last_fill = lot.average_price
        self.result.events.append(EngineEvent(
            bar.symbol, bar.time, order.intent.reason, campaign.direction, price,
            campaign.n, campaign.id, order.intent.unit_id,
            known_at=bar.time if opening else bar.time_close))
        return capacity - quantity

    def queue_exit(self, campaign: Campaign, bar: Bar, reason: str,
                   unit: str | None = None) -> None:
        for order in self.pending(bar.symbol, "open"):
            self.cancel(order, bar.time, reason)
        if unit is None:
            campaign.closing = True
        pending = {o.intent.unit_id for o in self.pending(bar.symbol, "reduce")}
        for key, lot in list(self.portfolio.lots.items()):
            if lot.symbol == bar.symbol and (unit is None or key == unit) and key not in pending:
                self.submit(campaign, lot.quantity, bar, reason, "reduce", key)

    def cleanup(self, symbol: str) -> None:
        campaign = self.campaigns.get(symbol)
        if campaign and not any(p.symbol == symbol for p in self.portfolio.lots.values()):
            if not self.pending(symbol, "open") and not self.pending(symbol, "reduce"):
                del self.campaigns[symbol]
                self.strategies[symbol].on_exit()

    def open_unit(self, symbol: str, direction: int, reference: Decimal,
                  bar: Bar, capacity: int, opening: bool) -> int:
        campaign = self.campaigns.get(symbol)
        new = campaign is None
        n = self.strategies[symbol].levels().n
        price = self.broker.price(reference, "buy" if direction == 1 else "sell")
        quantity = (self.weekly_shares.get(symbol, 0) if campaign is None else campaign.shares)
        if campaign is None:
            if self.squeeze:
                quantity = unit_shares(self.sizing.capital, decimal(n or 0), self.risk_config)
                # Intraday equity ATR risk often implies leverage. Squeeze uses
                # the smaller whole-share risk budget and available cash budget.
                budget = (self.portfolio.available_cash - self.reserved_cash()
                          - self.config.fee_per_order)
                factor = (Decimal(1) if direction == 1
                          else self.config.initial_short_collateral - 1)
                affordable = max(0, int(budget / (price * factor + self.config.fee_per_share)))
                quantity = min(quantity, affordable)
            elif self.risk_config.position_sizing == "available_cash":
                budget = (self.portfolio.available_cash - self.reserved_cash()
                          - self.config.fee_per_order)
                quantity = max(0, int(budget / (price + self.config.fee_per_share)))
            else:
                quantity = min(quantity, unit_shares(self.sizing.capital, decimal(n or 0),
                                                     self.risk_config))
        reason: str | None = None
        if self.halted or (self.risk_config.position_sizing == "atr" and self.sizing.capital <= 0):
            reason = "account_halted"
        elif direction == -1 and (not self.strategy_config.allow_short
                                  or symbol not in self.config.shortable_symbols):
            reason = "short_not_permitted"
        elif n is None or quantity <= 0:
            reason = "insufficient_unit_size"
        occupied = [(lot.symbol, lot.direction) for lot in self.portfolio.lots.values()]
        keys = set(self.portfolio.lots)
        occupied.extend((o.intent.symbol, o.intent.direction)
                        for o in self.broker.get_open_orders()
                        if o.intent.intent == "open" and o.intent.unit_id not in keys)
        reason = reason or unit_limit_reason(self.risk_config, symbol, direction, occupied)
        factor = Decimal(1) if direction == 1 else self.config.initial_short_collateral - 1
        required = quantity * (price * factor + self.config.fee_per_share)
        if required + self.config.fee_per_order > (
            self.portfolio.available_cash - self.reserved_cash()
        ):
            reason = reason or "insufficient_cash"
        self.result.risk_decisions.append(RiskRecord(
            bar.time, symbol, 0 if reason else quantity, reason or "accepted",
            quantity * (campaign.n if campaign else decimal(n or 0))
            * decimal(self.strategy_config.atr_multiple)))
        if reason:
            self.result.rejections.append(Rejection(bar.time, symbol, reason))
            return capacity
        if campaign is None:
            self.campaign_counter += 1
            campaign = Campaign(f"campaign-{self.campaign_counter:06d}", symbol, direction,
                                decimal(n or 0), quantity)
            self.campaigns[symbol] = campaign
        campaign.units += 1
        unit = f"{campaign.id}-unit-{campaign.units}"
        stop = price - direction * decimal(self.strategy_config.atr_multiple) * campaign.n
        order = self.submit(campaign, quantity, bar, "entry" if new else "addition", "open",
                            unit, stop)
        return self.fill(order, reference, bar, capacity, opening)

    def levels(self, symbol: str) -> list[Decimal]:
        strategy = self.strategies[symbol]
        levels = strategy.levels()
        values = [levels.entry_long, levels.entry_short, strategy.shadow_exit_level()]
        values.extend(strategy.shadow_triggers())
        campaign = self.campaigns.get(symbol)
        if campaign:
            values.append(levels.exit_long if campaign.direction == 1 else levels.exit_short)
            values.extend(float(p.stop) for p in self.portfolio.lots.values()
                          if p.symbol == symbol and p.stop is not None)
            if self.squeeze:
                values.append(strategy.target)
            elif campaign.last_fill is not None:
                values.append(float(campaign.last_fill + campaign.direction
                                    * decimal(self.strategy_config.add_multiple) * campaign.n))
        return sorted({decimal(v) for v in values if v is not None and v > 0})

    def point(self, bar: Bar, price: Decimal, capacity: int, opening: bool,
              actual_started: bool, shadow_started: bool,
              phase: Literal["both", "reduce", "open"] = "both",
              ) -> tuple[int, bool, bool]:
        symbol = bar.symbol
        strategy = self.strategies[symbol]
        levels = strategy.levels()
        self.portfolio.marks[symbol] = price
        if phase != "open" and strategy.shadow_direction:
            stop = strategy.shadow_exit_level()
            if stop is not None and strategy.shadow_direction * (price - decimal(stop)) <= 0:
                direction = strategy.shadow_direction
                n = strategy.shadow_n
                winner = strategy.shadow_close(float(price))
                self.result.events.append(EngineEvent(
                    symbol, bar.time, "shadow_exit", direction, price,
                    decimal(n) if n is not None else None,
                    detail="winner" if winner else "nonwinning",
                    known_at=bar.time if opening else bar.time_close))
                shadow_started = True
        if (phase != "open" and not strategy.shadow_direction
                and not shadow_started and levels.n is not None):
            long, short = strategy.shadow_triggers()
            direction = (1 if long is not None and price >= decimal(long) else
                         -1 if short is not None and price <= decimal(short) else 0)
            if direction:
                strategy.shadow_entry(direction, float(price), levels.n)
                self.result.events.append(EngineEvent(
                    symbol, bar.time, "shadow_entry", direction, price, decimal(levels.n),
                    known_at=bar.time if opening else bar.time_close))
                shadow_started = True
        levels = strategy.levels()
        campaign = self.campaigns.get(symbol)
        if campaign and phase != "open":
            signal = strategy.close_signal
            if (self.squeeze and opening and signal is not None
                    and signal.known_at <= bar.time and self.trading_at(bar.time)
                    and signal.direction != campaign.direction
                    and self.strategy_config.squeeze_flip_on_opposite):
                self.queue_exit(campaign, bar, "opposite_signal")
            exit_level = levels.exit_long if campaign.direction == 1 else levels.exit_short
            if exit_level is not None and campaign.direction * (price - decimal(exit_level)) <= 0:
                self.queue_exit(campaign, bar, "strategy_exit")
            for key, lot in list(self.portfolio.lots.items()):
                if (lot.symbol == symbol and lot.stop is not None
                        and lot.direction * (price - lot.stop) <= 0):
                    self.queue_exit(campaign, bar, "protective_stop", key)
            if (self.squeeze and strategy.target is not None
                    and campaign.direction * (price - decimal(strategy.target)) >= 0):
                self.queue_exit(campaign, bar, "take_profit")
            reductions = self.pending(symbol, "reduce")
            if reductions:
                actual_started = True
            for order in reductions:
                capacity = self.fill(order, price, bar, capacity, opening)
            if campaign.closing or self.pending(symbol, "reduce"):
                actual_started = True
            self.cleanup(symbol)
        if phase == "reduce":
            return capacity, actual_started, shadow_started
        if self.squeeze:
            # A close signal lives for exactly the following eligible candle.
            # Never reuse it at an intrabar crossing or after a same-bar exit.
            signal = strategy.close_signal
            if opening and signal is not None:
                strategy.clear_signal()
                if (self.trading_at(bar.time) and signal.known_at <= bar.time
                        and symbol not in self.campaigns and capacity > 0):
                    capacity = self.open_unit(symbol, signal.direction, price, bar,
                                              capacity, True)
                    actual_started = True
            return capacity, actual_started, shadow_started
        if not self.trading_at(bar.time) or capacity <= 0:
            return capacity, actual_started, shadow_started
        campaign = self.campaigns.get(symbol)
        if campaign and not campaign.closing and not self.pending(symbol, "reduce"):
            for order in self.pending(symbol, "open"):
                capacity = self.fill(order, price, bar, capacity, opening)
            if (campaign.last_fill is not None and not self.pending(symbol, "open")
                    and not campaign.blocked_additions
                    and self.held_units(symbol) < self.risk_config.max_units):
                addition = (campaign.last_fill + campaign.direction
                            * decimal(self.strategy_config.add_multiple) * campaign.n)
                if campaign.direction * (price - addition) >= 0:
                    capacity = self.open_unit(symbol, campaign.direction, price, bar,
                                              capacity, opening)
        elif not campaign and not actual_started:
            direction = (1 if levels.entry_long is not None and price >= decimal(levels.entry_long)
                         else -1 if levels.entry_short is not None
                         and price <= decimal(levels.entry_short) else 0)
            if direction:
                capacity = self.open_unit(symbol, direction, price, bar, capacity, opening)
                actual_started = True
        return capacity, actual_started, shadow_started

    def path(self, bar: Bar, high_first: bool) -> None:
        capacity = int(Decimal(bar.volume) * self.config.participation_rate)
        actual_started = shadow_started = False
        price = decimal(bar.open)
        capacity, actual_started, shadow_started = self.point(
            bar, price, capacity, True, actual_started, shadow_started)
        endpoints = (bar.high, bar.low, bar.close) if high_first else (bar.low, bar.high, bar.close)
        for endpoint in endpoints:
            end = decimal(endpoint)
            # Recompute crossings after each fill: additions and stop adjustments
            # can introduce new levels inside the same monotonic segment.
            for _ in range(128):
                crossings = [v for v in self.levels(bar.symbol)
                             if (price < v <= end if end > price else end <= v < price)]
                next_price = ((min if end > price else max)(crossings) if crossings else end)
                capacity, actual_started, shadow_started = self.point(
                    bar, next_price, capacity, False, actual_started, shadow_started)
                price = next_price
                if price == end:
                    break
            else:
                raise ValueError("too many intrabar rule transitions")
        self.portfolio.marks[bar.symbol] = decimal(bar.close)

    def margin_check(self, bars: list[Bar], *, closing: bool = False) -> None:
        by_symbol = {b.symbol: replace(b, time=b.time_close) if closing else b for b in bars}
        violation = self.portfolio.available_cash < 0 or self.portfolio.equity <= 0
        if self.portfolio.short_liability:
            violation |= (self.portfolio.collateral < self.config.maintenance_short_collateral
                          * self.portfolio.short_liability)
        self.halted = violation
        if violation:
            for order in self.broker.get_open_orders():
                if order.intent.intent == "open":
                    self.cancel(order, bars[0].time_close if closing else bars[0].time,
                                "margin_breach")
            for symbol, campaign in self.campaigns.items():
                if campaign.direction == -1 and symbol in by_symbol:
                    self.queue_exit(campaign, by_symbol[symbol], "forced_cover")

    def batch_path(self, bars: list[Bar], high_first: bool) -> None:
        """Synchronize piecewise-linear paths; reductions win simultaneous ties.

        Extremum timestamps are unknowable. This explicit common phase clock
        avoids giving an alphabetical symbol an entire candle of priority.
        """
        ordered = sorted(bars, key=lambda b: b.symbol)
        capacities = {b.symbol: int(Decimal(b.volume) * self.config.participation_rate)
                      for b in ordered}
        actual = {b.symbol: False for b in ordered}
        shadow = actual.copy()

        def process(prices: dict[str, Decimal], opening: bool) -> None:
            for symbol, price in prices.items():
                self.portfolio.marks[symbol] = price
            for phase in ("reduce", "open"):
                for bar in ordered:
                    symbol = bar.symbol
                    capacities[symbol], actual[symbol], shadow[symbol] = self.point(
                        bar, prices[symbol], capacities[symbol], opening,
                        actual[symbol], shadow[symbol],
                        "reduce" if phase == "reduce" else "open")

        process({b.symbol: decimal(b.open) for b in ordered}, True)
        for segment in range(3):
            starts: dict[str, Decimal] = {}
            ends: dict[str, Decimal] = {}
            for bar in ordered:
                points = ((bar.open, bar.high, bar.low, bar.close) if high_first
                          else (bar.open, bar.low, bar.high, bar.close))
                starts[bar.symbol], ends[bar.symbol] = map(decimal, points[segment:segment + 2])
            progress = Decimal(0)
            for _ in range(max(128, 128 * len(ordered))):
                crossings: list[tuple[Decimal, str, Decimal]] = []
                for bar in ordered:
                    symbol = bar.symbol
                    start, end = starts[symbol], ends[symbol]
                    if start == end:
                        continue
                    for level in self.levels(symbol):
                        fraction = (level - start) / (end - start)
                        if progress < fraction <= 1:
                            crossings.append((fraction, symbol, level))
                next_progress = min((c[0] for c in crossings), default=Decimal(1))
                prices = {s: start + (ends[s] - start) * next_progress
                          for s, start in starts.items()}
                for fraction, symbol, level in crossings:
                    if fraction == next_progress:
                        prices[symbol] = level
                process(prices, False)
                progress = next_progress
                if progress == 1:
                    break
            else:
                raise ValueError("too many synchronized intrabar transitions")

    def can_transition(self, bar: Bar) -> bool:
        """Skip path cloning only when neither ordering can change rule state."""
        symbol = bar.symbol
        if self.pending(symbol, "open") or self.pending(symbol, "reduce"):
            return True
        strategy = self.strategies[symbol]
        levels = strategy.levels()
        campaign = self.campaigns.get(symbol)
        signal = strategy.close_signal
        if (self.squeeze and signal is not None and signal.known_at <= bar.time
                and self.trading_at(bar.time)):
            return True
        if campaign:
            exit_level = levels.exit_long if campaign.direction == 1 else levels.exit_short
            adverse = bar.low if campaign.direction == 1 else bar.high
            if exit_level is not None and campaign.direction * (adverse - exit_level) <= 0:
                return True
            for lot in self.portfolio.lots.values():
                if (lot.symbol == symbol and lot.stop is not None
                        and lot.direction * (decimal(adverse) - lot.stop) <= 0):
                    return True
            if (not self.squeeze and campaign.last_fill is not None
                    and not campaign.blocked_additions
                    and self.held_units(symbol) < self.risk_config.max_units):
                threshold = (campaign.last_fill + campaign.direction
                             * decimal(self.strategy_config.add_multiple) * campaign.n)
                favorable = bar.high if campaign.direction == 1 else bar.low
                if campaign.direction * (decimal(favorable) - threshold) >= 0:
                    return True
            if self.squeeze and strategy.target is not None:
                favorable = bar.high if campaign.direction == 1 else bar.low
                if campaign.direction * (favorable - strategy.target) >= 0:
                    return True
        elif ((levels.entry_long is not None and bar.high >= levels.entry_long)
              or (levels.entry_short is not None and bar.low <= levels.entry_short)):
            return True
        if strategy.shadow_direction:
            shadow_exit = strategy.shadow_exit_level()
            adverse = bar.low if strategy.shadow_direction == 1 else bar.high
            return (shadow_exit is not None
                    and strategy.shadow_direction * (adverse - shadow_exit) <= 0)
        long, short = strategy.shadow_triggers()
        return ((long is not None and bar.high >= long)
                or (short is not None and bar.low <= short))

    def before_batch(self, bars: list[Bar]) -> None:
        # Borrow accrues against preceding marks, before applying today's gap.
        day = local_time(bars[0].time).date()
        if self.borrow_day is not None and day > self.borrow_day:
            fee = (self.portfolio.short_liability * (self.config.borrow_rate or Decimal(0))
                   * Decimal((day - self.borrow_day).days) / 365)
            self.portfolio.cash -= fee
            self.portfolio.borrow_costs += fee
        self.borrow_day = day
        for bar in bars:
            self.portfolio.marks[bar.symbol] = decimal(bar.open)
        self.sizing.observe(local_time(bars[0].time), self.portfolio.equity)
        for bar in bars:
            if self.squeeze:
                strategy = self.strategies[bar.symbol]
                signal = strategy.close_signal
                campaign = self.campaigns.get(bar.symbol)
                if signal is not None and (
                    not self.trading_at(bar.time) or signal.known_at > bar.time
                    or (campaign is not None and (
                        signal.direction == campaign.direction
                        or not self.strategy_config.squeeze_flip_on_opposite))
                ):
                    strategy.clear_signal()
                continue
            week = local_time(bar.time).isocalendar()[:2]
            if self.weeks.get(bar.symbol) != week:
                self.weeks[bar.symbol] = week
                n = self.strategies[bar.symbol].publish_week()
                self.weekly_shares[bar.symbol] = unit_shares(
                    self.sizing.capital, decimal(n or 0), self.risk_config)
                self.result.events.append(EngineEvent(
                    bar.symbol, bar.time, "weekly_snapshot", n=decimal(n) if n else None,
                    detail=str(self.weekly_shares[bar.symbol]), known_at=bar.time))
        self.margin_check(bars)

    def batch(self, bars: list[Bar], session_closes: dict[tuple[str, date], datetime],
              daily: dict[tuple[str, date], tuple[float, float, float]]) -> None:
        # Keep original observation labels for indicator publication. Opening
        # executions in a reopening candle cannot precede the verified restart.
        execution_bars = ([replace(bar, time=next_tradable_at(bar.time)) for bar in bars]
                          if self.market_halts else bars)
        self.before_batch(execution_bars)
        if any(self.can_transition(bar) for bar in execution_bars):
            first, second = self.fork(), self.fork()
            first.batch_path(execution_bars, True)
            second.batch_path(execution_bars, False)
            chosen = first if first.portfolio.equity <= second.portfolio.equity else second
            for bar in bars:
                if ([f for f in first.result.fills if f.symbol == bar.symbol]
                        != [f for f in second.result.fills if f.symbol == bar.symbol]
                        or any(getattr(first.strategies[bar.symbol], name)
                               != getattr(second.strategies[bar.symbol], name)
                               for name in ("shadow_direction", "shadow_price", "shadow_n",
                                            "last_winner"))):
                    chosen.result.execution_ambiguities.append(ExecutionAmbiguity(
                        bar.time, bar.symbol, "lower_equity_of_two_synchronized_ohlc_paths"))
            self.commit(chosen)
        else:
            for bar in bars:
                self.portfolio.marks[bar.symbol] = decimal(bar.close)
        for bar in bars:
            strategy = self.strategies[bar.symbol]
            if self.squeeze:
                for order in self.pending(bar.symbol, "open"):
                    self.cancel(order, bar.time_close, "entry_bar_expired")
                self.cleanup(bar.symbol)
            strategy.update_bar(bar)
            signal = strategy.close_signal
            if signal is not None:
                self.result.events.append(EngineEvent(
                    bar.symbol, signal.known_at, "close_signal", signal.direction,
                    n=decimal(signal.atr), known_at=signal.known_at))
            key = (bar.symbol, local_time(bar.time).date())
            if bar.time_close == session_closes[key]:
                strategy.update_session(*daily[key])
            levels = strategy.levels()
            self.result.events.append(EngineEvent(
                bar.symbol, bar.time_close, "levels_published", n=decimal(levels.n)
                if levels.n else None,
                detail=str({**asdict(levels), "previous_breakout_winner": strategy.last_winner}),
                known_at=bar.time_close))
        self.sizing.observe(local_time(bars[0].time), self.portfolio.equity)
        self.margin_check(bars, closing=True)
        at = bars[0].time_close
        if self.trade_start is None or at >= self.trade_start:
            self.result.equity.append(EquitySnapshot(
                at, self.portfolio.cash, self.portfolio.equity, self.portfolio.exposure,
                self.portfolio.net_exposure, self.portfolio.short_liability,
                self.portfolio.collateral, self.portfolio.borrow_costs, self.sizing.capital))

    def finish(self) -> BacktestResult:
        self.result.positions = [replace(p) for p in self.portfolio.lots.values()]
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
            "realized_pnl_after_fees": (self.portfolio.realized_pnl - self.portfolio.fees
                                        - self.portfolio.borrow_costs),
            "fees": self.portfolio.fees,
            "borrow_costs": self.portfolio.borrow_costs,
            "total_return": self.portfolio.equity / self.risk_config.starting_cash - 1,
            "max_drawdown": drawdown,
            "fills": len(self.result.fills),
            "trade_count": sum(f.intent == "open" for f in self.result.fills),
            "open_positions": len(self.portfolio.positions),
            "pending_orders": len(self.broker.get_open_orders()),
            "execution_ambiguities": len(self.result.execution_ambiguities),
            "gross_exposure": self.portfolio.exposure,
            "net_exposure": self.portfolio.net_exposure,
            "short_liability": self.portfolio.short_liability,
            "collateral": self.portfolio.collateral,
        }
        return self.result


def _sessions(bars: list[Bar], calendar: Calendar, interval: int) -> tuple[
    list[Bar], dict[tuple[str, date], datetime],
    dict[tuple[str, date], tuple[float, float, float]],
]:
    grouped: dict[tuple[str, date], list[Bar]] = {}
    for bar in bars:
        if calendar.eligible(bar.time, "regular"):
            grouped.setdefault((bar.symbol, local_time(bar.time).date()), []).append(bar)
    accepted: list[Bar] = []
    closes: dict[tuple[str, date], datetime] = {}
    daily: dict[tuple[str, date], tuple[float, float, float]] = {}
    bounds_method = getattr(calendar, "bounds", None)
    for symbol in sorted({b.symbol for b in bars}):
        days = sorted(d for s, d in grouped if s == symbol)
        if not days:
            continue
        day = days[0]
        while day <= days[-1]:
            key = (symbol, day)
            samples = grouped.get(key, [])
            bounds = bounds_method(day, "regular") if bounds_method else None
            if bounds_method and bounds is not None:
                opening, closing = bounds
                delta = timedelta(minutes=interval)
                slots = int((closing - opening).total_seconds() // delta.total_seconds())
                expected = [opening + i * delta for i in range(slots)
                            if not getattr(calendar, "market_halts", False)
                            or not fully_halted(opening + i * delta, delta)]
                complete = [bar.time for bar in samples] == expected
                if not complete:
                    if day not in (days[0], days[-1]):
                        raise ValueError(f"incomplete internal exchange session: {symbol} {day}")
                    day += timedelta(days=1)
                    continue
            if samples:
                accepted.extend(samples)
                closes[key] = samples[-1].time_close
                daily[key] = (max(b.high for b in samples), min(b.low for b in samples),
                              samples[-1].close)
            day += timedelta(days=1)
    return sorted(accepted, key=lambda b: (b.time, b.symbol)), closes, daily


def run_backtest(bars: list[Bar], strategy_config: StrategyConfig,
                 risk_config: RiskConfig, execution_config: ExecutionConfig,
                 calendar: Calendar | None = None, *,
                 price_basis: PriceBasis = "action_free",
                 trade_start: datetime | None = None,
                 trade_end: datetime | None = None,
                 _validate_only: bool = False) -> BacktestResult:
    """Run one selected entry/exit pair; warmup updates rules without actual fills."""
    price_basis = validate_price_basis(price_basis)
    if risk_config.position_sizing == "available_cash" and strategy_config.allow_short:
        raise ValueError("available_cash sizing requires long-only trading")
    if strategy_config.entry_strategy == "squeeze_momentum" and (
        risk_config.max_units != 1 or risk_config.position_sizing != "atr"
    ):
        raise ValueError("squeeze_momentum requires ATR sizing and max_units=1 (no pyramiding)")
    for boundary in (trade_start, trade_end):
        if boundary is not None and boundary.utcoffset() is None:
            raise ValueError("trade boundaries must be timezone-aware")
    if trade_start and trade_end and trade_start >= trade_end:
        raise ValueError("trade_start must precede trade_end")
    ordered = sorted(bars, key=lambda b: (b.time, b.symbol))
    previous: dict[str, datetime] = {}
    for bar in ordered:
        if bar.time.utcoffset() is None or bar.time_close.utcoffset() is None:
            raise ValueError("bar timestamps must be timezone-aware")
        prices = (bar.open, bar.high, bar.low, bar.close)
        if (not bar.symbol or not all(isfinite(p) and p > 0 for p in prices)
                or type(bar.volume) is not int or bar.volume < 0
                or bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close)):
            raise ValueError("invalid OHLCV bar")
        if bar.time < previous.get(bar.symbol, bar.time):
            raise ValueError("duplicate or overlapping symbol bars")
        if (bar.time_close - bar.time != timedelta(minutes=strategy_config.interval_minutes)
                or bar.time.second or bar.time.microsecond
                or bar.time.minute % strategy_config.interval_minutes):
            raise ValueError("bar duration/alignment must match configured interval")
        previous[bar.symbol] = bar.time_close
    symbols = {b.symbol for b in ordered}
    validate_groups(risk_config, symbols)
    if strategy_config.allow_short and (not execution_config.shortable_symbols
                                       or execution_config.borrow_rate is None):
        raise ValueError("shorts require shortable_symbols and explicit borrow_rate")
    if calendar is None:
        from .data import ExchangeCalendar
        calendar = ExchangeCalendar(start=ordered[0].time.date() if ordered else None,
                                    end=ordered[-1].time_close.date() if ordered else None)
    ordered, session_closes, daily = _sessions(ordered, calendar, strategy_config.interval_minutes)
    if bars and not ordered:
        raise ValueError("no complete regular exchange sessions remain after boundary exclusion")
    replay = _Replay(strategy_config, risk_config, execution_config, symbols,
                     trade_start, trade_end)
    replay.market_halts = bool(getattr(calendar, "market_halts", False))
    previous_close: datetime | None = None
    for _, group in groupby(ordered, key=lambda b: b.time):
        batch = list(group)
        if len({b.time_close for b in batch}) != 1 or (
            previous_close is not None and batch[0].time < previous_close
        ):
            raise ValueError("cross-symbol boundaries must align without overlap")
        previous_close = batch[0].time_close
        if _validate_only:
            continue
        if trade_end is not None and batch[0].time_close > trade_end:
            break
        replay.batch(batch, session_closes, daily)
    result = replay.finish()
    result.price_basis = price_basis
    sizing_description = ("available-cash entry sizing"
                          if risk_config.position_sizing == "available_cash" else "weekly sizing")
    if replay.squeeze:
        sizing_description = "signal-bar ATR sizing capped by available cash; no pyramiding"
    result.research_warnings = research_warnings(price_basis) + [
        f"Equity adaptation: cash-funded longs, regular exchange sessions, {sizing_description}; "
        "OHLC paths and volume capacity are simulations, not tick execution."]
    halt_days = {halt.start.date() for halt in MARKET_HALTS}
    if replay.market_halts and any(bar.time.date() in halt_days for bar in ordered):
        result.research_warnings.append(
            f"Verified market-wide pauses ({POLICY_VERSION}): no fabricated halt candles; "
            "reopening uses the next observed bar's opening price at no earlier than the "
            "exchange restart. Restart timestamps are execution bounds, not observed "
            "security trade times; coarse bars still leave intrabar ordering uncertain."
        )
    if strategy_config.allow_short:
        result.research_warnings.append(
            "Short borrowing availability, rates, collateral and observed-mark margin checks "
            "are explicit research assumptions, not historical stock-loan data.")
    result.exit_strategy = strategy_config.exit_strategy
    result.strategy_settings = asdict(strategy_config)
    result.strategy_warnings = strategy_warnings(strategy_config.exit_strategy)
    if replay.squeeze:
        result.strategy_warnings.append(
            "Independent corrected LazyBear implementation: completed-bar signals, next-open "
            "entries, frozen signal ATR and first-fill brackets; not video-result replication. "
            "Dual-target/trailing exits are not modeled.")
    return result


def validate_backtest_inputs(bars: list[Bar], strategy_config: StrategyConfig,
                             risk_config: RiskConfig, execution_config: ExecutionConfig,
                             calendar: Calendar | None = None, *,
                             price_basis: PriceBasis = "action_free",
                             trade_start: datetime | None = None,
                             trade_end: datetime | None = None) -> None:
    """Perform the same complete preflight without simulating any candles."""
    run_backtest(bars, strategy_config, risk_config, execution_config, calendar,
                 price_basis=price_basis, trade_start=trade_start, trade_end=trade_end,
                 _validate_only=True)
