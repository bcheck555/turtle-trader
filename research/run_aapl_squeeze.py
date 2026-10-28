"""Compare fixed Squeeze rules on acquired AAPL minutes and video date windows.

Run from the repository root with ``uv run python -m research.run_aapl_squeeze``.
This runner never downloads data or optimizes parameters.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from research.run_squeeze_comparison import (
    ROOT,
    _save_run,
    _write_dataset,
    aggregate_minutes,
    buy_and_hold,
    checksum,
)
from turtle_trader import __version__
from turtle_trader.backtest import run_backtest
from turtle_trader.config import AppConfig, resolved_config
from turtle_trader.data import ExchangeCalendar, read_manifest
from turtle_trader.domain import Bar, ExecutionConfig, RiskConfig, StrategyConfig
from turtle_trader.reporting import jsonable
from turtle_trader.sessions import local_time
from turtle_trader.sweep import atomic_json

VIDEO_FIXED = {"total_return": 1.3329, "max_drawdown": 0.1788,
               "win_rate": 0.4749, "profit_factor": 1.355, "closed_trade_count": 219}


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime
    requested_start: date
    requested_end: date


def window_for_dates(bars: list[Bar], start: date, end: date) -> Window:
    """Resolve inclusive exchange dates to first open and last close, without truncation."""
    if start > end:
        raise ValueError("window start must not follow end")
    days = {local_time(bar.time).date() for bar in bars}
    if not days or min(days) > start or max(days) < end:
        raise ValueError("source history does not cover the requested window")
    selected = [bar for bar in bars if start <= local_time(bar.time).date() <= end]
    if not selected:
        raise ValueError("window contains no exchange sessions")
    return Window(selected[0].time, selected[-1].time_close, start, end)


def full_history_window(bars: list[Bar], warmup_bars: int = 120) -> Window:
    """Begin at the first session with at least 120 preceding completed intraday bars."""
    if warmup_bars < 1:
        raise ValueError("warmup_bars must be positive")
    preceding = 0
    previous_day: date | None = None
    for bar in bars:
        day = local_time(bar.time).date()
        if day != previous_day and preceding >= warmup_bars:
            return Window(bar.time, bars[-1].time_close, day,
                          local_time(bars[-1].time).date())
        previous_day = day
        preceding += 1
    raise ValueError("not enough complete sessions for intraday warmup")


def configurations() -> dict[str, AppConfig]:
    """Freeze variants before running; 3% stop-risk limit, no parameter search."""
    risk = RiskConfig(starting_cash=Decimal("10000"), unit_fraction=Decimal("0.015"),
                      max_units=1)
    base = StrategyConfig(entry_strategy="squeeze_momentum", exit_strategy="atr_bracket",
                          interval_minutes=30)
    execution = ExecutionConfig()
    variants = {"raw_long": (False, False), "adx_long": (True, False),
                "wma_long": (False, True), "both_long": (True, True)}
    configs = {
        name: AppConfig(replace(base, squeeze_use_adx=adx, squeeze_use_wma=wma), risk, execution)
        for name, (adx, wma) in variants.items()
    }
    short_execution = replace(execution, shortable_symbols=("AAPL",),
                              borrow_rate=Decimal("0.03"))
    short_strategy = replace(base, allow_short=True)
    configs["both_long_short"] = AppConfig(short_strategy, risk, short_execution)
    configs["both_long_short_no_slippage"] = AppConfig(
        short_strategy, risk, replace(short_execution, slippage_bps=Decimal(0)))
    legacy_strategy = replace(short_strategy, squeeze_bb_multiple=1.5)
    configs["both_long_short_legacy_bb"] = AppConfig(legacy_strategy, risk, short_execution)
    configs["both_long_short_legacy_bb_no_slippage"] = AppConfig(
        legacy_strategy, risk, replace(short_execution, slippage_bps=Decimal(0)))
    return configs


def _markdown(report: dict[str, Any]) -> str:
    def percent(value: Any) -> str:
        return "—" if value is None else f"{value * 100:+.2f}%"

    lines = ["# AAPL Squeeze Momentum comparison", "",
             "Fixed parameters; corrected LazyBear calculations, with explicitly labeled",
             "legacy-BB sensitivities. Acquired 1-minute data",
             "is aggregated to complete regular-session 30-minute bars. Signals publish",
             "at close and execute at the next available open. Initial stop 2× ATR(14),",
             "fixed target 3× ATR, ADX(14,14)>20 and WMA(120) confirmations.", "",
             f"Source sessions: {report['aggregation']['first_session']}–"
             f"{report['aggregation']['last_session']}; "
             f"{report['aggregation']['source_rows']:,} minute bars; "
             f"{report['aggregation']['output_bars']:,} 30-minute bars.", "",
             "All runs start flat with $10,000 and preceding history for warmup.",
             "Long sizing is cash capped. Short sensitivities assume every AAPL share",
             "is borrowable at 3% annually, initial collateral 150% of short value,",
             "and 130% maintenance. These are research assumptions, not historical",
             "stock-loan or broker facts. Short collateral can permit up to roughly",
             "2× equity gross short exposure. Stops target at most 3% of sizing capital",
             "before gaps/costs; available cash can reduce that allocation. Sizing",
             "capital retains existing annual/drawdown adjustments. Reversals are enabled.", "",
             "Costs: 5 bps slippage per fill and zero commissions, except the explicitly",
             "labeled zero-slippage sensitivity. Borrow fees still apply in that sensitivity.",
             "Legacy-BB variants use a 1.5 BB multiplier (KC remains 1.5) to test the",
             "documented original multiplier bug. This was selected before AAPL results,",
             "because the video's bug-fix toggle is unchecked in the Bitcoin demonstration.",
             "Its final AAPL toggle state is unverified; this is a hypothesis, not source parity.",
             "No partial-target or trailing-runner exits are included.", "",
             "| Window | Variant | Return | Max drawdown | Closed campaigns | Win rate | PF |",
             "|---|---|---:|---:|---:|---:|---:|"]
    for window, variants in report["results"].items():
        for name, summary in variants.items():
            pf = summary["profit_factor"]
            lines.append(f"| {window} | {name} | {percent(summary['total_return'])} | "
                         f"{percent(summary['max_drawdown'])} | {summary['closed_trade_count']} | "
                         f"{percent(summary['win_rate'])} | {'—' if pf is None else f'{pf:.3f}'} |")
    lines += ["", "## Windows", ""]
    for name, window in report["windows"].items():
        lines.append(f"- {name}: {window['start']} through {window['end']} "
                     "(last close included).")
    lines += ["", "The video shows conflicting date inputs and tester headers. Both the",
              "2020–2026 tester-header range and 2022–2026 input range are tested",
              "without choosing between them after seeing results. The held-out proxy",
              "begins 2025-10-01: the video's 2025-09-30 23:00 cutoff has an unverified",
              "timezone. This is a comparison period, not new evidence of unseen data.", "",
              "## Video comparison target", "",
              "The displayed single-target AAPL result is +133.29% return, 17.88% max",
              "drawdown, 47.49% winning trades, PF 1.355 and 219 trades. The later",
              "two-leg result (+145.31%, PF 1.453) counts entry legs separately and",
              "uses a trailing runner, so it is not an equivalent target for this runner.", "",
              "The legacy-width sensitivity does not establish modified-source parity.",
              "The modified Pine script's BB bug toggle, exact gray-cross transition",
              "semantics, ATR/ADX seeding, fills, costs, leverage, trailing behavior,",
              "effective dates and TradingView data adjustments remain potential",
              "differences. No result is claimed to reproduce their proprietary engine.", "",
              "Closed-campaign PF and win rate are fee-inclusive flat-to-flat cycles.",
              "Open positions remain marked and are excluded from closed statistics.",
              "Adjusted research data does not model actual dividend cash/share events.",
              "Exposure, fees, shorts/margin rejections, and OHLC ambiguity counts are",
              "available in each summary/audit; absolute returns are not exposure matched.", "",
              f"Fingerprint: `{report['fingerprint']}`.", ""]
    return "\n".join(lines)


def run_comparison(source_csv: Path, source_manifest: Path, output: Path, *,
                   windows: dict[str, tuple[date, date]] | None = None,
                   include_heldout: bool = True) -> dict[str, Any]:
    """Publish immutable per-window artifacts with full source/config identity."""
    manifest = read_manifest(source_manifest)
    if manifest.symbols != ("AAPL",):
        raise ValueError("AAPL comparison requires an AAPL-only source manifest")
    output.mkdir(parents=True, exist_ok=False)
    bars, aggregation = aggregate_minutes(source_csv, manifest)
    _write_dataset(bars, manifest, output)
    selected = {"full": full_history_window(bars)}
    requested = windows if windows is not None else {
        "video_2020": (date(2020, 1, 2), date(2026, 10, 16)),
        "video_2022": (date(2022, 1, 3), date(2026, 10, 15)),
    }
    selected.update({name: window_for_dates(bars, *bounds) for name, bounds in requested.items()})
    if include_heldout:
        selected["video_heldout_proxy"] = window_for_dates(
            bars, date(2025, 10, 1), date(2026, 10, 15))
    configs = configurations()
    provenance = {
        "source_csv": str(source_csv.resolve()), "source_manifest": str(source_manifest.resolve()),
        "source_csv_sha256": checksum(source_csv),
        "source_manifest_sha256": checksum(source_manifest),
        "derived_csv_sha256": checksum(output / "bars.csv"),
        "derived_manifest_sha256": checksum(output / "manifest.toml"),
        "runner_sha256": checksum(Path(__file__)),
        "shared_runner_sha256": checksum(ROOT / "research/run_squeeze_comparison.py"),
        "engine_version": __version__,
        "engine_sources": {str(path.relative_to(ROOT)): checksum(path)
                           for path in sorted((ROOT / "src/turtle_trader").glob("*.py"))},
        "code_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "configurations": {name: resolved_config(config) for name, config in configs.items()},
        "windows": jsonable(selected), "video_fixed_target": VIDEO_FIXED,
    }
    identity = json.dumps(jsonable(provenance), sort_keys=True).encode()
    fingerprint = hashlib.sha256(identity).hexdigest()
    provenance["fingerprint"] = fingerprint
    atomic_json(output / "provenance.json", provenance)
    report: dict[str, Any] = {"aggregation": aggregation, "fingerprint": fingerprint,
                              "windows": jsonable(selected), "results": {},
                              "video_fixed_target": VIDEO_FIXED}
    calendar = ExchangeCalendar(manifest.start_date, manifest.end_date)
    for name, window in selected.items():
        directory = output / name
        directory.mkdir()
        summaries = report["results"][name] = {}
        active = configs if name != "video_heldout_proxy" else {
            key: config for key, config in configs.items()
            if key in ("both_long", "both_long_short")}
        for variant, config in active.items():
            print(f"{name}/{variant}", flush=True)
            result = run_backtest(bars, config.strategy, config.risk, config.execution, calendar,
                                  price_basis=manifest.price_basis,
                                  trade_start=window.start, trade_end=window.end)
            summaries[variant] = _save_run(result, directory / variant)
            atomic_json(output / "comparison.json", report)
        benchmark_bars = [bar for bar in bars
                          if bar.time >= window.start and bar.time_close <= window.end]
        benchmark = buy_and_hold(benchmark_bars, ExecutionConfig())
        benchmark.price_basis = manifest.price_basis
        summaries["buy_and_hold"] = _save_run(benchmark, directory / "buy_and_hold")
        atomic_json(output / "comparison.json", report)
    (output / "report.md").write_text(_markdown(report), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-csv", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--no-heldout", action="store_true")
    args = parser.parse_args()
    report = run_comparison(args.source_csv, args.source_manifest, args.output_dir.resolve(),
                            include_heldout=not args.no_heldout)
    print(json.dumps({"output": str(args.output_dir.resolve()),
                      "fingerprint": report["fingerprint"]}, sort_keys=True))


if __name__ == "__main__":
    main()
