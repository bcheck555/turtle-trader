"""Run the production engine and stream its audit artifacts without a SQLite blob."""

import argparse
import csv
import gzip
import hashlib
import json
import subprocess
import time
import uuid
from dataclasses import fields
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from turtle_trader import __version__
from turtle_trader.backtest import BacktestResult, EquitySnapshot, run_backtest
from turtle_trader.config import AppConfig, load_config, resolved_config
from turtle_trader.data import load_dataset
from turtle_trader.domain import Fill
from turtle_trader.reporting import jsonable, summarize_result
from turtle_trader.sweep import atomic_json, run_metadata

ROOT = Path(__file__).resolve().parents[1]
TRADE_START = datetime(2016, 5, 11, 13, 30, tzinfo=UTC)


def checksum(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def research_config(exit_strategy: str, position_sizing: str = "available_cash",
                    allow_short: bool = False, borrow_rate: Decimal | None = None) -> AppConfig:
    """Keep legacy defaults; use the engine's supported ATR mode for shorts."""
    if allow_short and position_sizing != "atr":
        raise ValueError("shorts require ATR sizing; available_cash remains long-only")
    if allow_short and borrow_rate is None:
        raise ValueError("shorts require an explicit annual borrow rate")
    overrides = [
        "strategy.interval_minutes=1",
        f'strategy.exit_strategy="{exit_strategy}"',
        'strategy.chandelier_timeframe="session"',
        f'risk.position_sizing="{position_sizing}"',
        f"strategy.allow_short={str(allow_short).lower()}",
    ]
    if borrow_rate is not None:
        overrides.extend(['execution.shortable_symbols=["QQQ"]',
                          f'execution.borrow_rate="{borrow_rate}"'])
    return load_config(ROOT / "configs/system2-available-cash.toml", overrides)


def stream_result(result: BacktestResult, output: Path) -> None:
    columns = sorted({"record_type"} | {f.name for f in fields(EquitySnapshot)}
                     | {f.name for f in fields(Fill)})
    with (output / "report.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for kind, observations in (("equity", result.equity), ("fill", result.fills)):
            for observation in observations:
                writer.writerow({"record_type": kind, **jsonable(observation)})
    with gzip.open(output / "events.jsonl.gz", "wt", encoding="utf-8", compresslevel=1) as handle:
        for event in result.events:
            handle.write(json.dumps(jsonable(event), separators=(",", ":"), allow_nan=False) + "\n")
    atomic_json(output / "fills.json", result.fills)
    atomic_json(output / "audit.json", {
        "metrics": result.metrics,
        "positions": result.positions,
        "orders": result.orders,
        "order_states": result.order_states,
        "risk_decisions": result.risk_decisions,
        "rejections": result.rejections,
        "cancellations": result.cancellations,
        "execution_ambiguities": result.execution_ambiguities,
        "price_basis": result.price_basis,
        "research_warnings": result.research_warnings,
        "exit_strategy": result.exit_strategy,
        "strategy_settings": result.strategy_settings,
        "strategy_warnings": result.strategy_warnings,
    })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exit_strategy", choices=("turtle_channel", "chandelier"))
    parser.add_argument("output", type=Path)
    parser.add_argument("--position-sizing", choices=("available_cash", "atr"),
                        default="available_cash")
    parser.add_argument("--allow-short", action="store_true")
    parser.add_argument("--borrow-rate", type=Decimal, help="annual fractional rate, e.g. 0.03")
    args = parser.parse_args()
    config = research_config(args.exit_strategy, args.position_sizing,
                             args.allow_short, args.borrow_rate)
    started = time.monotonic()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)

    def status(phase: str) -> None:
        atomic_json(output / "status.json", {
            "phase": phase, "at": datetime.now(UTC),
            "elapsed_seconds": round(time.monotonic() - started, 2),
        })

    status("loading")
    atomic_json(output / "config.json", resolved_config(config))
    source = ROOT / "runs/qqq-1m-20160223-20261023"
    data = load_dataset(source / "bars.csv", source / "manifest.toml")
    metadata = run_metadata(config, data, TRADE_START, None)
    metadata.update({
        "artifact_format": "streamed-equity-fill-csv-and-gzip-events-v1",
        "runner_sha256": checksum(Path(__file__)),
        "engine_sources": {str(p.relative_to(ROOT)): checksum(p)
                           for p in sorted((ROOT / "src/turtle_trader").glob("*.py"))},
        "code_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "engine_version": __version__,
        "input_csv_sha256": checksum(source / "bars.csv"),
        "data_warnings": data.warnings,
    })
    atomic_json(output / "metadata.json", metadata)
    status("simulating")
    result = run_backtest(data.bars, config.strategy, config.risk, config.execution,
                          trade_start=TRADE_START, price_basis=data.manifest.price_basis)
    summary = summarize_result(result)
    atomic_json(output / "engine-summary.json", summary)
    status("exporting")
    stream_result(result, output)
    artifacts = {name: checksum(output / name) for name in (
        "config.json", "metadata.json", "engine-summary.json", "report.csv",
        "events.jsonl.gz", "fills.json", "audit.json",
    )}
    record = {
        "status": "complete", "run_id": str(uuid.uuid4()),
        "fingerprint": metadata["run_fingerprint"], "summary": summary,
        "artifacts_sha256": artifacts,
        "counts": {"equity": len(result.equity), "events": len(result.events),
                   "fills": len(result.fills)},
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }
    atomic_json(output / "run.json", record)
    status("complete")
    print(json.dumps(jsonable(record), sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
