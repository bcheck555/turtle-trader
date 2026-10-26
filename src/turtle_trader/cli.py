"""Offline CLI. All network execution and live modes are intentionally absent."""

import argparse
import base64
import csv
import hashlib
import json
import sys
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from importlib.resources import files
from pathlib import Path
from typing import Any

from turtle_trader import __version__
from turtle_trader.backtest import run_backtest
from turtle_trader.config import load_config
from turtle_trader.data import Dataset, load_dataset
from turtle_trader.persistence import RunStore
from turtle_trader.reporting import (
    export_report_csv,
    export_report_json,
    jsonable,
    summarize_result,
)
from turtle_trader.strategy import Strategy


def source_hash() -> str:
    root = Path(__file__).resolve().parents[2] / "reference"
    if root.is_dir():
        content = (root / "revelio.pine").read_bytes()
        provenance = json.loads((root / "provenance.json").read_text())
        original = base64.b64decode((root / "revelio.original.base64").read_bytes())
    else:
        resources = files("turtle_trader").joinpath("reference")
        content = resources.joinpath("revelio.pine").read_bytes()
        provenance = json.loads(resources.joinpath("provenance.json").read_text())
        original = base64.b64decode(resources.joinpath("revelio.original.base64").read_bytes())
    normalized_hash = hashlib.sha256(content).hexdigest()
    if normalized_hash != provenance["stored_sha256"]:
        raise ValueError("normalized reference checksum mismatch")
    original_hash = hashlib.sha256(original).hexdigest()
    if original_hash != provenance["original_sha256"]:
        raise ValueError("original reference checksum mismatch")
    return original_hash


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


def _replay(data: Dataset, config: Any) -> dict[str, Any]:
    strategies = {symbol: Strategy(config.strategy) for symbol in data.manifest.symbols}
    events = [strategies[bar.symbol].on_bar(bar) for bar in data.bars]
    return {"events": events, "warnings": data.warnings, "manifest": data.manifest,
            "external_parity_verified": False}


def _save(args: argparse.Namespace, kind: str, config: object, data: Dataset,
          result: object) -> str:
    reference_hash = source_hash()
    config_hash = hashlib.sha256(
        json.dumps(jsonable(config), sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
    payload = result if isinstance(result, dict) else {"result": result}
    payload["metadata"] = {"engine_version": __version__, "config_sha256": config_hash,
                           "data_sha256": data.data_hash, "source_sha256": reference_hash,
                           "execution": "offline", "external_parity_verified": False}
    return RunStore(args.database).save(kind, config, data.data_hash, payload, reference_hash)


def _demo(directory: Path) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / "bars.csv"
    manifest = directory / "data.toml"
    if output.exists() or manifest.exists():
        raise ValueError("demo destination already contains bars.csv or data.toml")
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(75)]
    days = [d for d in days if d.weekday() < 5][:35]
    with output.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["symbol", "timestamp", "open", "high", "low", "close", "volume"])
        for index, day in enumerate(days):
            price = 100 if index < 23 else 100 + (index - 22) * 2
            at = datetime.combine(day, datetime.min.time(), UTC).replace(hour=15)
            writer.writerow(["DEMO", at.isoformat(), price, price+1, price-1, price, 10000])
            writer.writerow(["DEMO", (at+timedelta(minutes=5)).isoformat(),
                             price, price+1, price-1, price, 10000])
    manifest.write_text(
        '[data]\nfeed="synthetic-demo"\ninterval_minutes=5\ncoverage="custom"\n'
        'adjustment="split"\naction_free=true\nsynthetic=true\nsymbols=["DEMO"]\n'
        f'start_date="{days[0]}"\nend_date="{days[-1]}"\n', encoding="utf-8"
    )
    return {"csv": str(output), "manifest": str(manifest),
            "warning": "Synthetic smoke-test data; no market-performance interpretation"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="write deterministic synthetic smoke-test data")
    demo.add_argument("directory", type=Path)
    for name in ("validate", "import", "replay", "backtest", "walk-forward"):
        command = commands.add_parser(name)
        command.add_argument("csv", type=Path)
        command.add_argument("--manifest", type=Path, required=True)
        command.add_argument("--config", type=Path)
        command.add_argument("--database", type=Path, default=Path("runs/turtle.sqlite"))
        if name == "replay":
            command.add_argument("--allow-gaps", action="store_true")
        if name in ("backtest", "walk-forward"):
            command.add_argument("--output", type=Path, default=Path("runs/report.json"))
            command.add_argument("--benchmark", type=Path,
                                 help="CSV timestamp,price with total-return-adjusted prices")
            command.add_argument("--trade-start")
            command.add_argument("--trade-end")
        if name == "walk-forward":
            command.add_argument("--fold-days", type=int, default=30)
            command.add_argument("--warmup-days", type=int, default=60)
    report = commands.add_parser("report")
    report.add_argument("run_id")
    report.add_argument("--database", type=Path, default=Path("runs/turtle.sqlite"))
    report.add_argument("--output", type=Path, default=Path("runs/report.json"))
    report.add_argument("--benchmark", type=Path)
    return parser


def _run(args: argparse.Namespace) -> object:
    if args.command == "demo":
        return _demo(args.directory)
    if args.command == "report":
        record = RunStore(args.database).load(args.run_id)
        result = record["result"]
        benchmark = _benchmark(args.benchmark)
        export_report_json(result, args.output, benchmark)
        export_report_csv(result, args.output.with_suffix(".csv"))
        return {"run_id": args.run_id, "report": str(args.output)}
    config = load_config(args.config)
    strict = not (args.command == "replay" and args.allow_gaps)
    data = load_dataset(args.csv, args.manifest, strict=strict)
    if config.strategy.interval_minutes != data.manifest.interval_minutes:
        raise ValueError("strategy interval must match dataset interval")
    if args.command == "validate":
        return {"bars": len(data.bars), "data_hash": data.data_hash, "warnings": data.warnings}
    if args.command == "import":
        result = {"bars": data.bars, "manifest": data.manifest, "warnings": data.warnings}
        return {"run_id": _save(args, "import", config, data, result)}
    if args.command == "replay":
        return {"run_id": _save(args, "replay", config, data, _replay(data, config))}
    if data.manifest.coverage != "custom" and (
        config.execution.market_hours != data.manifest.coverage
    ):
        raise ValueError("execution hours must match declared data coverage")
    start = _aware(args.trade_start)
    end = _aware(args.trade_end)
    if start is not None and end is not None and start >= end:
        raise ValueError("trade_start must precede trade_end")
    benchmark = _benchmark(args.benchmark)
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
    records = []
    for index, (trade_start, trade_end) in enumerate(windows):
        result = run_backtest(data.bars, config.strategy, config.risk, config.execution,
                              trade_start=trade_start, trade_end=trade_end)
        report = summarize_result(result, benchmark)
        bundle = {**asdict(result), "report": report, "warnings": data.warnings,
                  "manifest": data.manifest, "trade_start": trade_start, "trade_end": trade_end}
        run_id = _save(args, args.command, config, data, bundle)
        target = args.output
        if args.command == "walk-forward":
            target = target.with_name(f"{target.stem}-{index:03}{target.suffix}")
        target.parent.mkdir(parents=True, exist_ok=True)
        export_report_json(bundle, target, benchmark)
        export_report_csv(bundle, target.with_suffix(".csv"))
        records.append({"run_id": run_id, "report": str(target)})
    return records[0] if args.command == "backtest" else records


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        value = _run(args)
        print(json.dumps(jsonable(value), allow_nan=False, sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, TypeError, ArithmeticError) as exc:
        print(json.dumps({"level": "error", "message": str(exc)}, allow_nan=False),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
