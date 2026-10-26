"""Reproducible, isolated backtests and bounded parameter sweeps."""

from __future__ import annotations

import csv
import hashlib
import itertools
import json
import os
import tomllib
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from importlib.resources import files
from pathlib import Path
from typing import Any

from turtle_trader import __version__
from turtle_trader.backtest import run_backtest, validate_backtest_inputs
from turtle_trader.config import (
    AppConfig,
    config_from_mapping,
    deviations,
    load_config,
    resolved_config,
)
from turtle_trader.data import Dataset
from turtle_trader.persistence import RunStore
from turtle_trader.reporting import (
    export_report_csv,
    export_report_json,
    jsonable,
    summarize_result,
)

SCHEMA_VERSION = 2
RANK_METRICS = frozenset({
    "total_return", "max_drawdown", "ending_equity", "closed_trade_count", "win_rate",
    "profit_factor", "average_exposure_fraction", "max_exposure_fraction", "fees", "borrow_costs",
})
LOWER_IS_BETTER = frozenset({"max_drawdown", "fees", "borrow_costs"})


def rule_hash() -> str:
    """Hash the specification actually installed alongside the implementation."""
    return hashlib.sha256(files("turtle_trader").joinpath("rule_spec.md").read_bytes()).hexdigest()


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        jsonable(value), sort_keys=True, allow_nan=False, separators=(",", ":")
    ).encode()).hexdigest()


def _file_hash(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def run_metadata(
    config: AppConfig, data: Dataset, start: datetime | None, end: datetime | None,
    benchmark: Sequence[tuple[datetime, Decimal]] | None = None,
) -> dict[str, Any]:
    identity = {
        "schema_version": SCHEMA_VERSION, "engine_version": __version__,
        "rule_version": "faith-equities-2.0", "rule_sha256": rule_hash(),
        "data_sha256": data.data_hash,
        "manifest": jsonable(data.manifest), "resolved_config": resolved_config(config),
        "trade_start": jsonable(start), "trade_end": jsonable(end),
        "benchmark_sha256": digest(benchmark) if benchmark is not None else None,
    }
    return {
        **identity, "run_fingerprint": digest(identity), "execution": "offline",
        "canonical_deviations": deviations(config),
        "execution_assumptions": [
            "Equity adaptation; regular exchange sessions and cash-funded longs.",
            "Weekly snapshots; campaign N and intended unit shares frozen at entry.",
            "OHLC ambiguity selects the lower closing-equity simulated path.",
            "Short availability, borrowing and margin are explicit research assumptions.",
        ],
    }


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(jsonable(value), sort_keys=True,
                                        allow_nan=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def execute_run(
    data: Dataset, config: AppConfig, directory: Path,
    start: datetime | None = None, end: datetime | None = None,
    benchmark: Sequence[tuple[datetime, Decimal]] | None = None,
) -> dict[str, Any]:
    """Execute one combination in its own directory and SQLite database."""
    directory.mkdir(parents=True, exist_ok=True)
    metadata = run_metadata(config, data, start, end, benchmark)
    atomic_json(directory / "config.json", resolved_config(config))
    result = run_backtest(data.bars, config.strategy, config.risk, config.execution,
                          trade_start=start, trade_end=end, price_basis=data.manifest.price_basis)
    bundle = {
        **asdict(result), "schema_version": SCHEMA_VERSION, "metadata": metadata,
        "config": resolved_config(config), "manifest": data.manifest,
        "warnings": data.warnings, "trade_start": start, "trade_end": end,
    }
    summary = summarize_result(bundle, benchmark)
    run_id = RunStore(directory / "run.sqlite").save(
        "backtest", config, data.data_hash, bundle, metadata["rule_sha256"]
    )
    export_report_json(bundle, directory / "report.json", benchmark)
    export_report_csv(bundle, directory / "report.csv")
    record = {
        "schema_version": SCHEMA_VERSION, "status": "completed", "run_id": run_id,
        "fingerprint": metadata["run_fingerprint"], "parameters": resolved_config(config),
        "summary": summary, "report": "report.json", "database": "run.sqlite",
        "artifacts": {name: _file_hash(directory / name) for name in (
            "report.json", "report.csv", "config.json", "run.sqlite",
        )},
    }
    atomic_json(directory / "run.json", record)
    return record


def _set(raw: dict[str, Any], key: str, value: Any) -> None:
    parts = key.split(".")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"grid keys must be section.setting paths: {key}")
    section = raw.get(parts[0])
    if not isinstance(section, dict) or parts[1] not in section:
        raise ValueError(f"unknown grid parameter: {key}")
    section[parts[1]] = value


def expand_grid(
    config: AppConfig, path: Path, max_runs: int = 100, *, base_path: Path | None = None,
    overrides: Sequence[str] | None = None,
) -> list[AppConfig]:
    if type(max_runs) is not int or max_runs < 1:
        raise ValueError("max_runs must be positive")
    with path.open("rb") as handle:
        grid = tomllib.load(handle)
    if set(grid) != {"parameters"} or not isinstance(grid["parameters"], dict):
        raise ValueError("grid requires only a [parameters] table")
    parameters = grid["parameters"]
    if not parameters or any(not isinstance(v, list) or not v for v in parameters.values()):
        raise ValueError("grid parameters require nonempty candidate arrays")
    size = 1
    for values in parameters.values():
        size *= len(values)
    if size > max_runs:
        raise ValueError(f"grid contains {size} runs; explicitly raise --max-runs above {max_runs}")
    names = sorted(parameters)
    combinations = []
    seen: set[str] = set()
    for values in itertools.product(*(parameters[name] for name in names)):
        raw = resolved_config(config)
        for name, value in zip(names, values, strict=True):
            _set(raw, name, value)
        candidate = (load_config(base_path, overrides,
                                 values=dict(zip(names, values, strict=True)))
                     if overrides is not None else config_from_mapping(raw))
        fingerprint = digest(resolved_config(candidate))
        if fingerprint in seen:
            raise ValueError("grid contains duplicate combinations after configuration resolution")
        seen.add(fingerprint)
        combinations.append(candidate)
    return combinations


@contextmanager
def _exclusive(directory: Path) -> Iterator[None]:
    # Advisory locks are released by the OS after crashes, allowing safe resume.
    import fcntl

    with (directory / ".sweep.lock").open("a") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("sweep output directory is in use") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _completed(directory: Path, fingerprint: str) -> dict[str, Any] | None:
    try:
        result = json.loads((directory / "run.json").read_text())
        if not isinstance(result, dict) or not isinstance(result.get("summary"), dict):
            return None
        artifacts = result.get("artifacts", {})
        if not isinstance(artifacts, dict):
            return None
        if (result.get("status") == "completed" and result.get("fingerprint") == fingerprint
                and set(artifacts) == {"report.json", "report.csv", "config.json", "run.sqlite"}
                and all(_file_hash(directory / name) == checksum
                        for name, checksum in artifacts.items())):
            return dict(result)
    except (OSError, ValueError, TypeError):
        pass
    return None


def _aggregate(directory: Path, records: Sequence[Mapping[str, Any]], rank_by: str) -> None:
    def sort_key(record: Mapping[str, Any]) -> tuple[bool, float, str]:
        value = record.get("summary", {}).get(rank_by)
        valid = isinstance(value, (int, float)) and not isinstance(value, bool)
        return (not valid, (float(value) if rank_by in LOWER_IS_BETTER else -float(value))
                if valid else 0.0, str(record["fingerprint"]))
    ordered = [{**record,
                "report": str(Path(str(record["directory"])) / "report.json"),
                "database": str(Path(str(record["directory"])) / "run.sqlite")}
               for record in sorted(records, key=sort_key)]
    atomic_json(directory / "summary.json", {
        "schema_version": SCHEMA_VERSION, "rank_by": rank_by,
        "ranking_scope": "specified historical test window", "runs": ordered,
    })
    columns = ["status", "fingerprint", "directory", "error", "report", "database",
               "parameters", *sorted(RANK_METRICS)]
    temporary = directory / ".summary.csv.tmp"
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for record in ordered:
            summary = record.get("summary", {})
            writer.writerow({
                **{key: record.get(key, "") for key in columns[:6]},
                "parameters": json.dumps(jsonable(record.get("parameters")), sort_keys=True),
                **{key: summary.get(key) for key in RANK_METRICS},
            })
    temporary.replace(directory / "summary.csv")


def run_sweep(
    data: Dataset, configs: Sequence[AppConfig], directory: Path, *, workers: int = 1,
    resume: bool = False, rank_by: str = "total_return", start: datetime | None = None,
    end: datetime | None = None,
    benchmark: Sequence[tuple[datetime, Decimal]] | None = None,
) -> dict[str, Any]:
    if type(workers) is not int or not 1 <= workers <= 32:
        raise ValueError("workers must be between 1 and 32")
    if rank_by not in RANK_METRICS:
        raise ValueError(f"unknown ranking metric: {rank_by}")
    if not configs:
        raise ValueError("sweep requires at least one combination")
    for config in configs:
        if config.strategy.interval_minutes != data.manifest.interval_minutes:
            raise ValueError("every grid strategy interval must match dataset interval")
        validate_backtest_inputs(data.bars, config.strategy, config.risk, config.execution,
                                 price_basis=data.manifest.price_basis,
                                 trade_start=start, trade_end=end)
    identities = [run_metadata(config, data, start, end, benchmark) for config in configs]
    fingerprints = [identity["run_fingerprint"] for identity in identities]
    if len(set(fingerprints)) != len(fingerprints):
        raise ValueError("duplicate sweep combinations")
    signature = digest(fingerprints)
    if not resume:
        try:
            directory.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise ValueError("sweep destination exists; choose a new directory or --resume"
                             ) from exc
    elif not directory.is_dir():
        raise ValueError("resume requires an existing sweep directory")
    with _exclusive(directory):
        manifest_path = directory / "manifest.json"
        records: list[dict[str, Any]] = []
        if resume:
            manifest = json.loads(manifest_path.read_text())
            if not isinstance(manifest, dict):
                raise ValueError("saved sweep manifest must be an object")
            if manifest.get("schema_version") != 2 or manifest.get("signature") != signature:
                raise ValueError("resume parameters, data or implementation differ from saved sweep"
                                 )
        for config, identity in zip(configs, identities, strict=True):
            fingerprint = identity["run_fingerprint"]
            child = directory / fingerprint
            old = _completed(child, fingerprint) if resume else None
            records.append({**(old or {"status": "pending", "fingerprint": fingerprint,
                                      "parameters": resolved_config(config)}),
                            "directory": fingerprint})
        manifest = {"schema_version": 2, "signature": signature, "runs": records}
        atomic_json(manifest_path, manifest)

        def save(index: int, result: dict[str, Any] | None, error: Exception | None) -> None:
            if error is not None:
                records[index].update(status="failed", error=f"{type(error).__name__}: {error}")
                atomic_json(directory / fingerprints[index] / "run.json", records[index])
            else:
                records[index] = {**(result or {}), "directory": fingerprints[index]}
            atomic_json(manifest_path, manifest)

        pending = [i for i, record in enumerate(records) if record["status"] != "completed"]
        for i in pending:
            (directory / fingerprints[i]).mkdir(exist_ok=True)
            records[i]["status"] = "running"
        atomic_json(manifest_path, manifest)
        if workers == 1:
            for i in pending:
                try:
                    result = execute_run(data, configs[i], directory / fingerprints[i],
                                         start, end, benchmark)
                except Exception as exc:  # Each failed experiment is an explicit result.
                    save(i, None, exc)
                else:
                    save(i, result, None)
        else:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(execute_run, data, configs[i], directory / fingerprints[i],
                                       start, end, benchmark): i for i in pending}
                for future in as_completed(futures):
                    i = futures[future]
                    try:
                        result = future.result()
                    except Exception as exc:
                        save(i, None, exc)
                    else:
                        save(i, result, None)
        _aggregate(directory, records, rank_by)
        failed = sum(record["status"] != "completed" for record in records)
        return {"schema_version": 2, "directory": str(directory), "runs": len(records),
                "failed": failed, "exit_code": 1 if failed else 0,
                "summary": str(directory / "summary.json")}
