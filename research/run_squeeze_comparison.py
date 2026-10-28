"""Compare fixed Squeeze variants on cached minute data; never acquire market data."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from turtle_trader import __version__
from turtle_trader.backtest import BacktestResult, EquitySnapshot, run_backtest
from turtle_trader.config import AppConfig, resolved_config
from turtle_trader.data import DataManifest, ExchangeCalendar, read_manifest, validate_bar
from turtle_trader.domain import Bar, ExecutionConfig, Fill, RiskConfig, StrategyConfig
from turtle_trader.halts import POLICY_VERSION, fully_halted
from turtle_trader.reporting import jsonable, summarize_result
from turtle_trader.sessions import local_time
from turtle_trader.sweep import atomic_json

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "runs/qqq-1m-20160223-20261023"
INTERVAL = timedelta(minutes=30)
MINUTE = timedelta(minutes=1)


class SessionCalendar(Protocol):
    market_halts: bool

    def bounds(self, day: date, market_hours: str) -> tuple[datetime, datetime] | None: ...

    def eligible(self, at: datetime, market_hours: str = "regular") -> bool: ...


def checksum(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _minute_rows(path: Path, symbol: str) -> Iterable[Bar]:
    previous: datetime | None = None
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            at = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
            bar = Bar(row["symbol"], at, at + MINUTE,
                      *(float(row[key]) for key in ("open", "high", "low", "close")),
                      int(row["volume"]))
            validate_bar(bar)
            if bar.symbol != symbol:
                raise ValueError("comparison requires exactly the manifest's single symbol")
            if at.second or at.microsecond or (previous is not None and at <= previous):
                raise ValueError("minute rows must be aligned, unique, and sorted")
            previous = at
            yield bar


def aggregate_minutes(path: Path, manifest: DataManifest,
                      calendar: SessionCalendar | None = None) -> tuple[list[Bar], dict[str, Any]]:
    """Stream one symbol into complete, session-aligned 30-minute candles.

    A candle crossing a verified pause contains only observed tradable minutes.
    Missing ordinary minutes invalidate a session. Only incomplete first/last
    observed sessions may be dropped; gaps in internal sessions are errors.
    """
    if manifest.interval_minutes != 1 or len(manifest.symbols) != 1:
        raise ValueError("comparison requires a single-symbol one-minute manifest")
    calendar = calendar or ExchangeCalendar(manifest.start_date, manifest.end_date)
    symbol = manifest.symbols[0]
    bars: list[Bar] = []
    incomplete: list[date] = []
    observed_days: list[date] = []
    stats: dict[str, Any] = {"source_rows": 0, "outside_regular_hours": 0,
                             "halted_minute_slots": 0, "incomplete_candles": 0}
    current_day: date | None = None
    samples: list[Bar] = []

    def finish(day: date, values: list[Bar]) -> None:
        bounds = calendar.bounds(day, "regular")
        if bounds is None:
            return
        opening, closing = bounds
        observed_days.append(day)
        expected = [opening + i * MINUTE for i in range(int((closing - opening) / MINUTE))
                    if not calendar.market_halts or not fully_halted(opening + i * MINUTE, MINUTE)]
        stats["halted_minute_slots"] += int((closing - opening) / MINUTE) - len(expected)
        by_time = {bar.time: bar for bar in values}
        complete_session = list(by_time) == expected
        converted: list[Bar] = []
        at = opening
        while at + INTERVAL <= closing:
            slots = [minute for minute in expected if at <= minute < at + INTERVAL]
            if slots and all(minute in by_time for minute in slots):
                selected = [by_time[minute] for minute in slots]
                converted.append(Bar(symbol, at, at + INTERVAL, selected[0].open,
                                     max(bar.high for bar in selected),
                                     min(bar.low for bar in selected), selected[-1].close,
                                     sum(bar.volume for bar in selected)))
            elif slots:
                stats["incomplete_candles"] += 1
            at += INTERVAL
        if not complete_session or at != closing:
            incomplete.append(day)
        else:
            bars.extend(converted)

    for bar in _minute_rows(path, symbol):
        stats["source_rows"] += 1
        day = local_time(bar.time).date()
        if not calendar.eligible(bar.time):
            stats["outside_regular_hours"] += 1
            continue
        if calendar.market_halts and fully_halted(bar.time, MINUTE):
            raise ValueError(f"observed minute lies entirely inside a market halt: {bar.time}")
        if current_day is not None and day != current_day:
            finish(current_day, samples)
            samples = []
        current_day = day
        samples.append(bar)
    if current_day is not None:
        finish(current_day, samples)
    boundary_days = {observed_days[0], observed_days[-1]} if observed_days else set()
    internal = set(incomplete) - boundary_days
    if internal:
        raise ValueError(f"incomplete internal minute sessions: {sorted(internal)}")
    days = sorted({local_time(bar.time).date() for bar in bars})
    if not days:
        raise ValueError("no complete sessions available")
    day = days[0]
    included = set(days)
    while day <= days[-1]:
        if calendar.bounds(day, "regular") is not None and day not in included:
            raise ValueError(f"missing internal exchange session: {day}")
        day += timedelta(days=1)
    stats.update({"output_bars": len(bars), "complete_sessions": len(days),
                  "dropped_boundary_sessions": [day.isoformat() for day in incomplete],
                  "first_session": days[0].isoformat(), "last_session": days[-1].isoformat(),
                  "halt_policy": POLICY_VERSION})
    return bars, stats


def split_sessions(bars: list[Bar]) -> tuple[datetime, datetime]:
    """Return common evaluation start and held-out start; never split a session."""
    starts: dict[date, datetime] = {}
    for bar in bars:
        starts.setdefault(local_time(bar.time).date(), bar.time)
    days = sorted(starts)
    if len(days) < 2:
        raise ValueError("at least two complete sessions are required")
    index = min(len(days) - 1, max(1, len(days) * 7 // 10))
    # All strategies share a 120-session burn-in, enough for daily Turtle channels.
    # Small fixtures can still exercise the runner without claiming warmed performance.
    warmup_days = min(120, max(0, index - 1))
    return starts[days[warmup_days]], starts[days[index]]


def _write_dataset(bars: list[Bar], source: DataManifest, output: Path) -> None:
    with (output / "bars.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("symbol", "timestamp", "open", "high", "low", "close", "volume"))
        for bar in bars:
            writer.writerow((bar.symbol, bar.time.isoformat(), bar.open, bar.high,
                             bar.low, bar.close, bar.volume))
    settings = asdict(source)
    settings.update(interval_minutes=30, coverage="regular", timestamp_convention="bar_open",
                    start_date=local_time(bars[0].time).date(),
                    end_date=local_time(bars[-1].time).date())
    lines = ["[data]"]
    for key, value in settings.items():
        if isinstance(value, date):
            rendered = value.isoformat()
        elif isinstance(value, tuple):
            rendered = json.dumps(list(value))
        else:
            rendered = json.dumps(value)
        lines.append(f"{key} = {rendered}")
    (output / "manifest.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def buy_and_hold(bars: list[Bar], execution: ExecutionConfig,
                 starting_cash: Decimal = Decimal("10000")) -> BacktestResult:
    """Cash-funded whole-share buy at first open; hold and mark at each close."""
    if not bars:
        raise ValueError("buy-and-hold requires bars")
    slip = execution.slippage_bps / Decimal(10000)
    price = Decimal(str(bars[0].open)) * (1 + slip)
    quantity = max(0, int((starting_cash - execution.fee_per_order)
                          / (price + execution.fee_per_share)))
    fee = execution.fee_per_order + quantity * execution.fee_per_share if quantity else Decimal(0)
    cash = starting_cash - quantity * price - fee
    result = BacktestResult(metrics={"starting_cash": starting_cash})
    if quantity:
        result.fills.append(Fill("buyhold", bars[0].symbol, "buy", quantity, price, fee,
                                 bars[0].time, "buy_and_hold", campaign_id="buyhold"))
    for bar in bars:
        exposure = quantity * Decimal(str(bar.close))
        result.equity.append(EquitySnapshot(bar.time_close, cash, cash + exposure, exposure))
    result.metrics["open_quantity"] = quantity
    result.strategy_settings = {"entry_strategy": "buy_and_hold", "exit_strategy": "turtle_channel"}
    return result


def _save_run(result: BacktestResult, output: Path) -> dict[str, Any]:
    output.mkdir()
    summary = summarize_result(result)
    summary.update({"fill_count": len(result.fills), "open_position_count": len(result.positions),
                    "rejections": dict(Counter(item.reason for item in result.rejections)),
                    "execution_ambiguity_count": len(result.execution_ambiguities)})
    if "open_quantity" in result.metrics:
        summary["open_position_count"] = int(result.metrics["open_quantity"] > 0)
    atomic_json(output / "summary.json", summary)
    atomic_json(output / "fills.json", result.fills)
    atomic_json(output / "audit.json", {"metrics": result.metrics, "positions": result.positions,
                                        "rejections": result.rejections,
                                        "cancellations": result.cancellations,
                                        "execution_ambiguities": result.execution_ambiguities})
    with (output / "equity.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("timestamp", "cash", "equity", "exposure"))
        for point in result.equity:
            writer.writerow((point.time.isoformat(), point.cash, point.equity, point.exposure))
    return summary


def _markdown(report: dict[str, Any]) -> str:
    def percent(value: Any) -> str:
        return "—" if value is None else f"{value * 100:.2f}%"

    lines = ["# Squeeze Momentum: cached QQQ comparison", "",
             "Independent implementation; fixed parameters, no optimization. Long-only,",
             "cash-capped whole-share positions; one fixed 3× ATR target and 2× ATR stop.",
             "Adjusted historical data and simulated 5 bps slippage per fill. This does not",
             "reproduce the video's shorting, leverage, capital/risk path, or advanced exits.", "",
             f"Source: {report['aggregation']['first_session']}–"
             f"{report['aggregation']['last_session']}; "
             f"{report['aggregation']['complete_sessions']} complete sessions.",
             f"Evaluation begins {report['evaluation_start']}; held-out begins "
             f"{report['held_out_start']}. Shared 120-session burn-in before evaluation.",
             "Held-out runs start flat with fresh $10,000 and preceding indicator history.", "",
             "| Period | Strategy | Return | Max drawdown | Closed campaigns | Win rate | PF |",
             "|---|---|---:|---:|---:|---:|---:|"]
    for period, variants in report["results"].items():
        for name, summary in variants.items():
            pf = summary["profit_factor"]
            lines.append(f"| {period} | {name} | {percent(summary['total_return'])} | "
                         f"{percent(summary['max_drawdown'])} | {summary['closed_trade_count']} | "
                         f"{percent(summary['win_rate'])} | {'—' if pf is None else f'{pf:.2f}'} |")
    lines += ["", "Returns include marked open positions; campaign win rates/PF include only",
              "closed flat-to-flat campaigns. Buy-and-hold remains open, so its closed-trade",
              "statistics are undefined. Per-run JSON includes fills, fees, exposure,",
              "rejections, open positions, and ambiguous execution counts.", "",
              "The Turtle reference retains daily ATR(20), weekly sizing, and drawdown",
              "capital adjustments. Its unit fraction is 0.015 and max units is one, but",
              "its original insufficient-cash rejection is not Squeeze's cash-size cap.",
              "Treat this as a strategy reference, not identical sizing mechanics.", "",
              "The 70/30 split is chronological by included sessions, before the shared",
              "burn-in. These fixed variants are descriptive comparisons, not evidence",
              "that selecting the best held-out result will generalize.", ""]
    return "\n".join(lines)


def run_comparison(source_csv: Path, source_manifest: Path, output: Path) -> dict[str, Any]:
    """Create a unique artifact directory and run fixed chronological comparisons."""
    manifest = read_manifest(source_manifest)
    output.mkdir(parents=True, exist_ok=False)
    bars, aggregation = aggregate_minutes(source_csv, manifest)
    _write_dataset(bars, manifest, output)
    start, heldout = split_sessions(bars)
    execution = ExecutionConfig()
    risk = RiskConfig(starting_cash=Decimal("10000"), unit_fraction=Decimal("0.015"), max_units=1)
    variants = {"raw": (False, False), "adx": (True, False),
                "wma": (False, True), "adx_wma": (True, True)}
    configs = {
        name: AppConfig(StrategyConfig(entry_strategy="squeeze_momentum",
                                       exit_strategy="atr_bracket", interval_minutes=30,
                                       squeeze_use_adx=adx, squeeze_use_wma=wma), risk, execution)
        for name, (adx, wma) in variants.items()
    }
    configs["turtle_s2"] = AppConfig(StrategyConfig(interval_minutes=30), risk, execution)
    provenance = {"source_csv": str(source_csv.resolve()), "source_manifest": str(source_manifest),
                  "source_csv_sha256": checksum(source_csv),
                  "source_manifest_sha256": checksum(source_manifest),
                  "derived_csv_sha256": checksum(output / "bars.csv"),
                  "derived_manifest_sha256": checksum(output / "manifest.toml"),
                  "runner_sha256": checksum(Path(__file__)), "engine_version": __version__,
                  "engine_sources": {str(p.relative_to(ROOT)): checksum(p)
                                     for p in sorted((ROOT / "src/turtle_trader").glob("*.py"))},
                  "code_commit": subprocess.check_output(
                      ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                  "configurations": {name: resolved_config(config)
                                     for name, config in configs.items()}}
    identity = json.dumps(jsonable(provenance), sort_keys=True).encode()
    fingerprint = hashlib.sha256(identity).hexdigest()
    provenance["fingerprint"] = fingerprint
    atomic_json(output / "provenance.json", provenance)
    report: dict[str, Any] = {"aggregation": aggregation, "evaluation_start": start.isoformat(),
                              "held_out_start": heldout.isoformat(), "fingerprint": fingerprint,
                              "results": {}}
    calendar = ExchangeCalendar(manifest.start_date, manifest.end_date)
    periods = {"full": (start, None), "in_sample": (start, heldout),
               "held_out": (heldout, None)}
    for period, (trade_start, trade_end) in periods.items():
        directory = output / period
        directory.mkdir()
        summaries = report["results"][period] = {}
        for name, config in configs.items():
            print(f"{period}/{name}", flush=True)
            result = run_backtest(bars, config.strategy, config.risk, config.execution, calendar,
                                  price_basis=manifest.price_basis,
                                  trade_start=trade_start, trade_end=trade_end)
            summaries[name] = _save_run(result, directory / name)
        selected = [bar for bar in bars if bar.time >= trade_start
                    and (trade_end is None or bar.time_close <= trade_end)]
        benchmark = buy_and_hold(selected, execution, risk.starting_cash)
        benchmark.price_basis = manifest.price_basis
        summaries["buy_and_hold"] = _save_run(benchmark, directory / "buy_and_hold")
        atomic_json(output / "comparison.json", report)
    (output / "report.md").write_text(_markdown(report), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-csv", type=Path, default=DEFAULT_SOURCE / "bars.csv")
    parser.add_argument("--source-manifest", type=Path, default=DEFAULT_SOURCE / "manifest.toml")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = run_comparison(args.source_csv, args.source_manifest, args.output_dir.resolve())
    print(json.dumps({"output": str(args.output_dir.resolve()),
                      "fingerprint": report["fingerprint"]}, sort_keys=True))


if __name__ == "__main__":
    main()
