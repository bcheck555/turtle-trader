"""Offline research and explicit historical-data downloads; no live order execution."""

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import sys
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Never

import httpx

from turtle_trader import __version__
from turtle_trader.alpaca import DownloadRequest, DownloadResult, download_alpaca
from turtle_trader.backtest import run_backtest
from turtle_trader.config import AppConfig, deviations, load_config, resolved_config
from turtle_trader.data import Dataset, ExchangeCalendar, load_dataset
from turtle_trader.persistence import RunStore
from turtle_trader.reporting import (
    export_report_csv,
    export_report_json,
    jsonable,
    summarize_result,
)
from turtle_trader.research import research_warnings, strategy_warnings
from turtle_trader.sweep import (
    atomic_json,
    expand_grid,
    rule_hash,
    run_metadata,
    run_sweep,
)


def _aware(value: str | None) -> datetime | None:
    if value is None:
        return None
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("trade window timestamps require a UTC offset")
    return result.astimezone(UTC)


def _benchmark(path: Path | None) -> list[tuple[datetime, Decimal]] | None:
    if path is None:
        return None
    points: list[tuple[datetime, Decimal]] = []
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["timestamp", "price"]:
            raise ValueError("benchmark requires timestamp,price columns (total-return prices)")
        for row in reader:
            at = _aware(row["timestamp"])
            price = Decimal(row["price"])
            if at is None or not price.is_finite() or price <= 0:
                raise ValueError("invalid benchmark point")
            if points and at <= points[-1][0]:
                raise ValueError("benchmark must be strictly chronological")
            points.append((at, price))
    if not points:
        raise ValueError("empty benchmark")
    return points


def _protect_outputs(outputs: list[Path], inputs: list[Path | None]) -> None:
    """Reject both report files when either aliases an input, including hard links."""
    for output in outputs:
        for source in inputs:
            if source is None:
                continue
            if (output.resolve() == source.resolve()
                    or output.exists() and source.exists() and output.samefile(source)):
                raise ValueError(f"report output would overwrite an input file: {output}")


def _strategy_identity(config: AppConfig) -> dict[str, Any]:
    return {"entry_strategy": config.strategy.entry_strategy,
            "exit_strategy": config.strategy.exit_strategy,
            "strategy_settings": asdict(config.strategy),
            "strategy_warnings": strategy_warnings(config.strategy.exit_strategy),
            "canonical_deviations": deviations(config)}


def _replay(data: Dataset, config: AppConfig) -> dict[str, Any]:
    result = run_backtest(data.bars, config.strategy, config.risk, config.execution,
                          price_basis=data.manifest.price_basis)
    return {"events": result.events, "orders": result.orders,
            "rejections": result.rejections, "warnings": data.warnings, "manifest": data.manifest,
            **_strategy_identity(config),
            "price_basis": data.manifest.price_basis,
            "research_warnings": research_warnings(data.manifest.price_basis)}


def _save(args: argparse.Namespace, kind: str, config: AppConfig, data: Dataset,
          result: object) -> str:
    specification_hash = rule_hash()
    config_hash = hashlib.sha256(
        json.dumps(jsonable(config), sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
    payload = result if isinstance(result, dict) else {"result": result}
    payload["schema_version"] = 2
    payload["config"] = resolved_config(config)
    start = payload.get("trade_start")
    end = payload.get("trade_end")
    if start is not None and not isinstance(start, datetime):
        raise ValueError("trade_start must be a datetime")
    if end is not None and not isinstance(end, datetime):
        raise ValueError("trade_end must be a datetime")
    payload["metadata"] = {**run_metadata(config, data, start, end,
                                         _benchmark(getattr(args, "benchmark", None))),
                           "engine_version": __version__, "config_sha256": config_hash,
                           "price_basis": data.manifest.price_basis,
                           "research_warnings": research_warnings(data.manifest.price_basis),
                           **_strategy_identity(config)}
    return RunStore(args.database).save(kind, config, data.data_hash, payload, specification_hash)


def _demo(directory: Path) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / "bars.csv"
    manifest = directory / "data.toml"
    if output.exists() or manifest.exists():
        raise ValueError("demo destination already contains bars.csv or data.toml")
    calendar = ExchangeCalendar(date(2025, 1, 1), date(2025, 8, 1))
    days = [date(2025, 1, 1) + timedelta(days=i) for i in range(180)]
    days = [day for day in days if calendar.bounds(day, "regular") is not None][:90]
    with output.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["symbol", "timestamp", "open", "high", "low", "close", "volume"])
        for index, day in enumerate(days):
            price = 100 if index < 60 else 100 + (index - 59) * 2
            bounds = calendar.bounds(day, "regular")
            assert bounds is not None
            at, close = bounds
            while at + timedelta(minutes=5) <= close:
                writer.writerow(["DEMO", at.isoformat(), price, price+1, price-1, price, 10000])
                at += timedelta(minutes=5)
    manifest.write_text(
        '[data]\nfeed="synthetic-demo"\ninterval_minutes=5\ncoverage="regular"\n'
        'adjustment="split"\naction_free=true\nsynthetic=true\nsymbols=["DEMO"]\n'
        f'start_date="{days[0]}"\nend_date="{days[-1]}"\n', encoding="utf-8"
    )
    return {"csv": str(output), "manifest": str(manifest),
            "warning": "Synthetic smoke-test data; no market-performance interpretation"}


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise ValueError(message)


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    download = commands.add_parser("download", help="download historical data; never trades")
    providers = download.add_subparsers(dest="provider", required=True)
    alpaca = providers.add_parser("alpaca", help="Alpaca historical US equity bars")
    alpaca.add_argument("--symbols", nargs="+", required=True)
    alpaca.add_argument("--start", type=date.fromisoformat, required=True)
    alpaca.add_argument("--end", type=date.fromisoformat, required=True)
    alpaca.add_argument("--output", type=Path, required=True)
    alpaca.add_argument("--interval", type=int, choices=(1, 2, 3, 5, 6, 10, 15, 30), default=5,
                        help="candle interval in minutes (default: 5)")
    alpaca.add_argument("--feed", choices=("sip", "iex"), default="sip")
    alpaca.add_argument("--coverage", choices=("regular", "extended"), default="regular")
    demo = commands.add_parser("demo", help="write deterministic synthetic smoke-test data")
    demo.add_argument("directory", type=Path)
    for name in ("validate", "import", "replay", "backtest", "walk-forward", "sweep"):
        command = commands.add_parser(name)
        command.add_argument("csv", type=Path, nargs="?")
        command.add_argument("--manifest", type=Path)
        command.add_argument("--config", type=Path)
        command.add_argument("--set", dest="overrides", action="append", default=[],
                             help="override section.key with a TOML literal (repeatable)")
        command.add_argument("--print-config", action="store_true")
        if name != "sweep":
            command.add_argument("--database", type=Path, default=Path("runs/turtle-v2.sqlite"))
        command.add_argument("--output-dir", type=Path)
        if name in ("backtest", "walk-forward", "replay"):
            command.add_argument("--output", type=Path)
        if name in ("backtest", "walk-forward", "sweep"):
            command.add_argument("--benchmark", type=Path,
                                 help="CSV timestamp,price with total-return-adjusted prices")
            command.add_argument("--trade-start")
            command.add_argument("--trade-end")
        if name == "walk-forward":
            command.add_argument("--fold-days", type=int, default=30)
            command.add_argument("--warmup-days", type=int, default=60)
        if name == "sweep":
            command.add_argument("--grid", type=Path)
            command.add_argument("--workers", type=int, default=1)
            command.add_argument("--max-runs", type=int, default=100)
            command.add_argument("--resume", action="store_true")
            command.add_argument("--rank-by", default="total_return")
    report = commands.add_parser("report")
    report.add_argument("run_id")
    report.add_argument("--database", type=Path, default=Path("runs/turtle-v2.sqlite"))
    report.add_argument("--output", type=Path, default=Path("runs/report.json"))
    report.add_argument("--benchmark", type=Path)
    return parser


def _alpaca_client() -> httpx.Client:
    """Access credentials only for an explicitly invoked download command."""
    key = os.environ.get("APCA_API_KEY_ID", "")
    secret = os.environ.get("APCA_API_SECRET_KEY", "")
    if not key.strip() or not secret.strip():
        raise ValueError("Alpaca download requires APCA_API_KEY_ID and APCA_API_SECRET_KEY")
    if any(ord(c) < 32 or ord(c) > 126 for c in key + secret):
        raise ValueError("Alpaca credential format invalid")
    return httpx.Client(headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
                        timeout=httpx.Timeout(30, connect=10), follow_redirects=False)


def _run(args: argparse.Namespace) -> object:
    if args.command == "download":
        request = DownloadRequest(tuple(args.symbols), args.start, args.end, args.interval,
                                  args.feed, args.coverage)
        with _alpaca_client() as client:
            return download_alpaca(request, args.output, client=client)
    if args.command == "demo":
        return _demo(args.directory)
    if args.command == "report":
        _protect_outputs([args.output, args.output.with_suffix(".csv")],
                         [args.benchmark, args.database])
        record = RunStore(args.database).load(args.run_id)
        benchmark = _benchmark(args.benchmark)
        export_report_json(record, args.output, benchmark)
        export_report_csv(record, args.output.with_suffix(".csv"))
        return {"run_id": args.run_id, "report": str(args.output)}
    config = load_config(args.config, args.overrides)
    if args.print_config:
        return resolved_config(config)
    if args.csv is None or args.manifest is None:
        raise ValueError("csv and --manifest are required unless --print-config is used")
    if args.output_dir is not None and getattr(args, "output", None) is not None:
        raise ValueError("choose --output or --output-dir")
    data = load_dataset(args.csv, args.manifest,
                        strict=args.command in ("validate", "import"))
    if (args.command not in ("validate", "import")
            and config.strategy.interval_minutes != data.manifest.interval_minutes):
        raise ValueError("strategy interval must match dataset interval")
    if args.command == "validate":
        return {"bars": len(data.bars), "data_hash": data.data_hash, "warnings": data.warnings,
                **_strategy_identity(config),
                "price_basis": data.manifest.price_basis,
                "research_warnings": research_warnings(data.manifest.price_basis)}
    if args.command != "sweep" and args.output_dir is not None:
        try:
            args.output_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise ValueError("output directory exists; choose a new run directory") from exc
        args.database = args.output_dir / "run.sqlite"
        args.output = args.output_dir / "report.json"
        atomic_json(args.output_dir / "config.json", resolved_config(config))
    if args.command == "import":
        import_result = {"bars": data.bars, "manifest": data.manifest, "warnings": data.warnings,
                         **_strategy_identity(config),
                         "price_basis": data.manifest.price_basis,
                         "research_warnings": research_warnings(data.manifest.price_basis)}
        return {"run_id": _save(args, "import", config, data, import_result)}
    if args.command == "replay":
        if args.output is not None:
            _protect_outputs([args.output, args.output.with_suffix(".csv")],
                             [args.csv, args.manifest, args.config, args.database])
        replay = _replay(data, config)
        run_id = _save(args, "replay", config, data, replay)
        if args.output is not None:
            export_report_json(replay, args.output)
            export_report_csv(replay, args.output.with_suffix(".csv"))
        result_record = {"run_id": run_id, "events": len(replay["events"]),
                         "fingerprint": replay["metadata"]["run_fingerprint"]}
        if args.output_dir is not None:
            atomic_json(args.output_dir / "run.json", result_record)
        return result_record
    start = _aware(args.trade_start)
    end = _aware(args.trade_end)
    if start is not None and end is not None and start >= end:
        raise ValueError("trade_start must precede trade_end")
    benchmark = _benchmark(args.benchmark)
    if args.command == "sweep":
        if args.grid is None or args.output_dir is None:
            raise ValueError("sweep requires --grid and --output-dir")
        configs = expand_grid(config, args.grid, args.max_runs,
                              base_path=args.config, overrides=args.overrides)
        for candidate in configs:
            if candidate.strategy.interval_minutes != data.manifest.interval_minutes:
                raise ValueError("every grid strategy interval must match dataset interval")
        return run_sweep(data, configs, args.output_dir, workers=args.workers,
                         resume=args.resume, rank_by=args.rank_by, start=start, end=end,
                         benchmark=benchmark)
    windows: list[tuple[datetime | None, datetime | None]] = [(start, end)]
    if args.command == "walk-forward":
        if args.fold_days < 1 or args.warmup_days < 1:
            raise ValueError("fold and warmup days must be positive")
        first = start or data.bars[0].time + timedelta(days=args.warmup_days)
        last = min(end or data.bars[-1].time_close, data.bars[-1].time_close)
        windows = []
        while first < last:
            following = min(first + timedelta(days=args.fold_days), last)
            windows.append((first, following))
            first = following
        if not windows:
            raise ValueError("insufficient data for requested walk-forward windows")
    targets = []
    for index in range(len(windows)):
        target = args.output or Path("runs/report.json")
        if args.command == "walk-forward":
            target = target.with_name(f"{target.stem}-{index:03}{target.suffix}")
        targets.append(target)
    _protect_outputs([path for target in targets for path in (target, target.with_suffix(".csv"))],
                     [args.csv, args.manifest, args.config, args.benchmark, args.database])
    records = []
    for index, (trade_start, trade_end) in enumerate(windows):
        result = run_backtest(data.bars, config.strategy, config.risk, config.execution,
                              trade_start=trade_start, trade_end=trade_end,
                              price_basis=data.manifest.price_basis)
        report = summarize_result(result, benchmark)
        bundle = {**asdict(result), "report": report, "warnings": data.warnings,
                  "manifest": data.manifest, "trade_start": trade_start, "trade_end": trade_end}
        run_id = _save(args, args.command, config, data, bundle)
        target = targets[index]
        target.parent.mkdir(parents=True, exist_ok=True)
        export_report_json(bundle, target, benchmark)
        export_report_csv(bundle, target.with_suffix(".csv"))
        record = {"run_id": run_id, "report": str(target), "summary": report,
                  "fingerprint": bundle["metadata"]["run_fingerprint"]}
        if args.output_dir is not None:
            atomic_json(args.output_dir / "run.json", record)
        records.append(record)
    return records[0] if args.command == "backtest" else records


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        value = _run(args)
        print(json.dumps(jsonable(value), allow_nan=False, sort_keys=True))
        if isinstance(value, DownloadResult):
            return value.exit_code
        return int(value.get("exit_code", 0)) if isinstance(value, dict) else 0
    except (ValueError, OSError, KeyError, TypeError, ArithmeticError, sqlite3.Error) as exc:
        print(json.dumps({"level": "error", "message": str(exc)}, allow_nan=False),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
