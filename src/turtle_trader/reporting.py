"""Small, offline report summaries and exports for completed runs."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from .research import (
    ADJUSTED_RESEARCH_WARNING,
    PriceBasis,
    validate_price_basis,
)
from .research import strategy_warnings as configured_strategy_warnings

_MISSING = object()


def _get(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _time(value: Any) -> Any:
    if isinstance(value, (tuple, list)) and value:
        return value[0]
    return _get(value, "time", _get(value, "timestamp"))


def _point_price(value: Any) -> Any:
    if isinstance(value, (tuple, list)) and len(value) > 1:
        return value[1]
    return _get(value, "price", _get(value, "close"))


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Decimal):
        return str(value) if value.is_finite() else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return _jsonable(value.value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, float) and _number(value) is None:
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def jsonable(value: object) -> Any:
    """Convert a result record or common research value to JSON-compatible data."""
    return _jsonable(value)


def _run_result(value: Any) -> Any:
    """Unwrap RunStore and CLI save wrappers, leaving result records intact."""
    return _run_layers(value)[-1]


def _run_layers(value: Any) -> list[Any]:
    layers = [value]
    current = value
    while isinstance(current, Mapping) and "result" in current:
        is_run = "run_id" in current
        is_saved_payload = "metadata" in current
        if not (is_run or is_saved_payload):
            break
        current = current["result"]
        layers.append(current)
    return layers


def _research_metadata(value: Any) -> tuple[PriceBasis, list[str]]:
    bases: set[PriceBasis] = set()
    warnings: list[str] = []
    containers: list[Any] = []
    for layer in _run_layers(value):
        containers.extend((layer, _get(layer, "metadata", {}), _get(layer, "manifest", {}),
                           _get(layer, "config", {})))
    for container in containers:
        raw_basis = _get(container, "price_basis")
        if raw_basis is not None:
            bases.add(validate_price_basis(raw_basis))
        raw_warnings = _get(container, "research_warnings", [])
        if not isinstance(raw_warnings, (list, tuple)) or any(
            not isinstance(warning, str) for warning in raw_warnings
        ):
            raise ValueError("research_warnings must be a list of strings")
        for warning in raw_warnings:
            if warning not in warnings:
                warnings.append(warning)
    if len(bases) > 1:
        raise ValueError("conflicting price basis metadata")
    basis = next(iter(bases)) if bases else "action_free"
    if basis == "adjusted_research" and ADJUSTED_RESEARCH_WARNING not in warnings:
        warnings.append(ADJUSTED_RESEARCH_WARNING)
    return basis, warnings


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, Mapping):
        return value
    return None


def _valid_strategy_setting(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int, Decimal, Path, Enum)):
        return not isinstance(value, Decimal) or value.is_finite()
    if isinstance(value, float):
        return _number(value) is not None
    if isinstance(value, (list, tuple)):
        return all(_valid_strategy_setting(item) for item in value)
    if isinstance(value, Mapping):
        return all(
            isinstance(key, str) and _valid_strategy_setting(item)
            for key, item in value.items()
        )
    return False


def _strategy_identity(value: Any) -> tuple[str, dict[str, Any], list[str]]:
    """Resolve strategy identity across result, metadata, and stored config."""
    layers = _run_layers(value)
    containers: list[Any] = []
    configs: list[Any] = []
    for layer in layers:
        containers.extend((layer, _get(layer, "metadata", {}), _get(layer, "manifest", {})))
        config = _get(layer, "config", _MISSING)
        if config is not _MISSING:
            configs.append(config)
            containers.append(config)
            containers.append(_get(config, "metadata", {}))

    selectors: list[Any] = []
    setting_sources: list[Mapping[str, Any]] = []
    warnings: list[str] = []
    for container in containers:
        selector = _get(container, "exit_strategy", _MISSING)
        if selector is not _MISSING:
            selectors.append(selector)

        settings = _get(container, "strategy_settings", _MISSING)
        if settings is not _MISSING:
            mapped = _as_mapping(settings)
            if mapped is None:
                raise ValueError("strategy_settings must be a mapping")
            setting_sources.append(mapped)
            if "exit_strategy" in mapped:
                selectors.append(mapped["exit_strategy"])

        strategy = _get(container, "strategy", _MISSING)
        if strategy is not _MISSING:
            mapped_strategy = _as_mapping(strategy)
            if mapped_strategy is None:
                raise ValueError("stored strategy config must be a mapping")
            setting_sources.append(mapped_strategy)
            if "exit_strategy" in mapped_strategy:
                selectors.append(mapped_strategy["exit_strategy"])

        if any(container is config for config in configs):
            mapped_config = _as_mapping(container)
            if mapped_config is not None and (
                "exit_strategy" in mapped_config
                or any(key.startswith("chandelier_") for key in mapped_config)
            ):
                setting_sources.append(mapped_config)

        raw_warnings = _get(container, "strategy_warnings", [])
        if raw_warnings is None:
            raw_warnings = []
        if not isinstance(raw_warnings, (list, tuple)) or any(
            not isinstance(warning, str) for warning in raw_warnings
        ):
            raise ValueError("strategy_warnings must be a list of strings")
        for warning in raw_warnings:
            if warning not in warnings:
                warnings.append(warning)

    if any(not isinstance(selector, str) or not selector for selector in selectors):
        raise ValueError("exit_strategy must be a nonempty strategy identifier")
    if len(set(selectors)) > 1:
        raise ValueError("conflicting exit_strategy metadata")
    exit_strategy = selectors[0] if selectors else "turtle_channel"

    merged_settings: dict[str, Any] = {}
    for candidate in setting_sources:
        for key, setting in candidate.items():
            if not isinstance(key, str) or not _valid_strategy_setting(setting):
                raise ValueError("strategy_settings contains an invalid setting")
            if key in merged_settings and _jsonable(merged_settings[key]) != _jsonable(setting):
                raise ValueError(f"conflicting strategy setting: {key}")
            merged_settings[key] = setting
    if "exit_strategy" in merged_settings and merged_settings["exit_strategy"] != exit_strategy:
        raise ValueError("conflicting exit_strategy metadata")
    for name in ("chandelier_length", "chandelier_atr_length"):
        if name in merged_settings and type(merged_settings[name]) is not int:
            raise ValueError(f"{name} must be an integer")
    if ("chandelier_multiple" in merged_settings
            and type(merged_settings["chandelier_multiple"]) not in (int, float)):
        raise ValueError("chandelier_multiple must be numeric")
    if ("chandelier_ratchet" in merged_settings
            and type(merged_settings["chandelier_ratchet"]) is not bool):
        raise ValueError("chandelier_ratchet must be boolean")
    if ("chandelier_timeframe" in merged_settings
            and merged_settings["chandelier_timeframe"] not in ("session", "intraday")):
        raise ValueError("chandelier_timeframe must be session or intraday")
    for warning in configured_strategy_warnings(exit_strategy):
        if warning not in warnings:
            warnings.append(warning)
    return exit_strategy, merged_settings, warnings


def _benchmark_return(benchmark: Iterable[Any], start: Any, end: Any) -> float | None:
    points = list(benchmark)
    keyed: dict[Any, float] = {}
    for point in points:
        at = _time(point)
        price = _number(_point_price(point))
        if at is not None and price is not None and price > 0:
            keyed[at] = price
    if start not in keyed or end not in keyed:
        return None
    return keyed[end] / keyed[start] - 1


def _trade_metrics(fills: Iterable[Any]) -> dict[str, Any]:
    """Match signed fills, allocating fees across flat-to-flat campaigns."""
    quantity: dict[str, int] = {}
    basis: dict[str, float] = {}
    cycle_pnl: dict[str, float] = {}
    realized_pnl = 0.0
    closed_cycles: list[float] = []

    for fill in fills:
        symbol = str(_get(fill, "symbol", ""))
        qty = int(_get(fill, "quantity", 0) or 0)
        price = _number(_get(fill, "price"))
        fee = _number(_get(fill, "fee", 0))
        fee = fee if fee is not None else 0.0
        side = str(_get(fill, "side", "")).lower()
        if not symbol or qty <= 0 or price is None:
            continue
        held = quantity.get(symbol, 0)
        if side not in ("buy", "sell"):
            continue
        signed = qty if side == "buy" else -qty
        if not held or held * signed > 0:
            direction = 1 if signed > 0 else -1
            basis[symbol] = basis.get(symbol, 0.0) + price * qty + direction * fee
            quantity[symbol] = held + signed
            cycle_pnl.setdefault(symbol, 0.0)
        else:
            closed = min(abs(held), qty)
            average_cost = basis[symbol] / abs(held)
            direction = 1 if held > 0 else -1
            net_pnl = direction * (price - average_cost) * closed - fee * closed / qty
            realized_pnl += net_pnl
            cycle_pnl[symbol] = cycle_pnl.get(symbol, 0.0) + net_pnl
            remaining = abs(held) - closed
            quantity[symbol] = direction * remaining
            basis[symbol] = average_cost * remaining
            if quantity[symbol] == 0:
                closed_cycles.append(cycle_pnl.pop(symbol, 0.0))
                basis.pop(symbol, None)
            if qty > closed:
                opened = qty - closed
                next_direction = 1 if signed > 0 else -1
                quantity[symbol] = next_direction * opened
                basis[symbol] = price * opened + next_direction * fee * opened / qty
                cycle_pnl[symbol] = 0.0

    winners = sum(pnl > 0 for pnl in closed_cycles)
    gross_profit = sum(pnl for pnl in closed_cycles if pnl > 0)
    gross_loss = -sum(pnl for pnl in closed_cycles if pnl < 0)
    return {
        "realized_pnl": realized_pnl,
        "closed_trade_count": len(closed_cycles),
        "win_rate": winners / len(closed_cycles) if closed_cycles else None,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
    }


def summarize_result(result: Any, benchmark: Iterable[Any] | None = None) -> dict[str, Any]:
    """Summarize fee-inclusive flat-to-flat trades and equity.

    Equity snapshot exposure is marked gross market value in currency units.
    Exposure fraction fields divide each observation by contemporaneous equity.
    ``gross_turnover_dollars`` is notional and ``turnover_multiple`` is notional
    divided by starting equity. Benchmark endpoints require exact timestamps;
    status reports whether they matched. Undefined ratios are ``None``.
    """
    price_basis, warnings = _research_metadata(result)
    exit_strategy, strategy_settings, strategy_warnings = _strategy_identity(result)
    canonical_deviations: list[str] = []
    for layer in _run_layers(result):
        for container in (layer, _get(layer, "metadata", {})):
            for change in _get(container, "canonical_deviations", []) or []:
                if not isinstance(change, str):
                    raise ValueError("canonical_deviations must contain strings")
                if change not in canonical_deviations:
                    canonical_deviations.append(change)
    result = _run_result(result)
    equity_points = list(_get(result, "equity", []) or [])
    fills = list(_get(result, "fills", []) or [])
    equities = [_number(_get(point, "equity")) for point in equity_points]
    valid_equity = [value for value in equities if value is not None]
    metrics = _get(result, "metrics", {}) or {}
    starting = _number(_get(metrics, "starting_cash"))
    if starting is None:
        starting = valid_equity[0] if valid_equity else None
    ending = valid_equity[-1] if valid_equity else None
    returns = (
        ending / starting - 1
        if starting is not None and ending is not None and starting != 0
        else None
    )
    peak: float | None = starting
    max_drawdown = 0.0 if valid_equity else None
    for value in valid_equity:
        peak = value if peak is None else max(peak, value)
        if peak and max_drawdown is not None:
            max_drawdown = max(max_drawdown, (peak - value) / abs(peak))

    trade_metrics = _trade_metrics(fills)
    gross_turnover = sum(
        abs(int(_get(fill, "quantity", 0) or 0)) * (_number(_get(fill, "price")) or 0.0)
        for fill in fills
    )
    exposure: list[float] = []
    exposure_fraction: list[float] = []
    for point, point_equity in zip(equity_points, equities, strict=True):
        exposure_value = _number(_get(point, "exposure"))
        if exposure_value is not None:
            exposure.append(abs(exposure_value))
            if point_equity is not None and point_equity != 0:
                exposure_fraction.append(abs(exposure_value) / abs(point_equity))
    if benchmark is None:
        benchmark_result = None
        benchmark_status = "not_provided"
    elif not equity_points:
        benchmark_result = None
        benchmark_status = "no_equity_points"
    else:
        benchmark_result = _benchmark_return(
            benchmark, _time(equity_points[0]), _time(equity_points[-1])
        )
        benchmark_status = "matched" if benchmark_result is not None else "endpoint_mismatch"

    return {
        "schema_version": 2,
        "starting_equity": starting,
        "ending_equity": ending,
        "total_return": returns,
        "max_drawdown": max_drawdown,
        **trade_metrics,
        "gross_turnover_dollars": gross_turnover,
        "gross_turnover": gross_turnover,
        "turnover_multiple": gross_turnover / starting if starting else None,
        "turnover": gross_turnover / starting if starting else None,
        "average_exposure_dollars": sum(exposure) / len(exposure) if exposure else None,
        "max_exposure_dollars": max(exposure) if exposure else None,
        "average_exposure": sum(exposure) / len(exposure) if exposure else None,
        "max_exposure": max(exposure) if exposure else None,
        "average_exposure_fraction": (
            sum(exposure_fraction) / len(exposure_fraction) if exposure_fraction else None
        ),
        "max_exposure_fraction": max(exposure_fraction) if exposure_fraction else None,
        "benchmark_return": benchmark_result,
        "benchmark_status": benchmark_status,
        "benchmark_excess_return": returns - benchmark_result
        if returns is not None and benchmark_result is not None else None,
        "price_basis": price_basis,
        "research_warnings": warnings,
        "exit_strategy": exit_strategy,
        "strategy_settings": _jsonable(strategy_settings),
        "strategy_warnings": strategy_warnings,
        "canonical_deviations": canonical_deviations,
        "entry_strategy": strategy_settings.get("entry_strategy", "turtle_s2"),
        "fees": sum(_number(_get(fill, "fee", 0)) or 0.0 for fill in fills),
        "borrow_costs": _number(_get(metrics, "borrow_costs", 0)) or 0.0,
    }


def export_report_json(
    result: Any, path: str | Path, benchmark: Iterable[Any] | None = None
) -> Path:
    """Write a self-contained JSON report and return its path."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    price_basis, warnings = _research_metadata(result)
    exit_strategy, strategy_settings, strategy_warnings = _strategy_identity(result)
    payload = {
        "schema_version": 2,
        "price_basis": price_basis,
        "research_warnings": warnings,
        "exit_strategy": exit_strategy,
        "strategy_settings": _jsonable(strategy_settings),
        "strategy_warnings": strategy_warnings,
        "summary": summarize_result(result, benchmark),
        "result": _jsonable(result),
    }
    output.write_text(
        json.dumps(payload, allow_nan=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output


def export_report_csv(result: Any, path: str | Path) -> Path:
    """Write equity/fill rows and repeat research-basis labels on each row."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    source = _run_result(result)
    price_basis, warnings = _research_metadata(result)
    exit_strategy, strategy_settings, strategy_warnings = _strategy_identity(result)
    strategy_settings_json = json.dumps(_jsonable(strategy_settings), separators=(",", ":"))
    strategy_warnings_json = json.dumps(strategy_warnings, separators=(",", ":"))
    rows: list[dict[str, Any]] = []
    for kind in ("equity", "fills"):
        for item in _get(source, kind, []) or []:
            row = _jsonable(item)
            rows.append({
                "record_type": "fill" if kind == "fills" else "equity",
                "price_basis": price_basis,
                "research_warnings": json.dumps(warnings, separators=(",", ":")),
                "exit_strategy": exit_strategy,
                "strategy_settings": strategy_settings_json,
                "strategy_warnings": strategy_warnings_json,
                **row,
            })
    if not rows:
        rows.append({
            "record_type": "metadata",
            "price_basis": price_basis,
            "research_warnings": json.dumps(warnings, separators=(",", ":")),
            "exit_strategy": exit_strategy,
            "strategy_settings": strategy_settings_json,
            "strategy_warnings": strategy_warnings_json,
        })
    columns = sorted({key for row in rows for key in row} | {"record_type"})
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted(columns), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return output
