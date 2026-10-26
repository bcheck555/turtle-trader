"""Small, offline report summaries and exports for completed runs."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any


def _get(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


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
    """Match long fills, allocating all fill fees across their quantities."""
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
        if side == "buy":
            basis[symbol] = basis.get(symbol, 0.0) + price * qty + fee
            quantity[symbol] = held + qty
            cycle_pnl.setdefault(symbol, 0.0)
        elif side == "sell" and held:
            closed = min(held, qty)
            average_cost = basis[symbol] / held
            net_pnl = (price - average_cost) * closed - fee
            realized_pnl += net_pnl
            cycle_pnl[symbol] = cycle_pnl.get(symbol, 0.0) + net_pnl
            quantity[symbol] = held - closed
            basis[symbol] = average_cost * (held - closed)
            if quantity[symbol] == 0:
                closed_cycles.append(cycle_pnl.pop(symbol, 0.0))
                basis.pop(symbol, None)

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
    peak: float | None = None
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
    }


def export_report_json(
    result: Any, path: str | Path, benchmark: Iterable[Any] | None = None
) -> Path:
    """Write a self-contained JSON report and return its path."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {"summary": summarize_result(result, benchmark), "result": _jsonable(result)}
    output.write_text(
        json.dumps(payload, allow_nan=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output


def export_report_csv(result: Any, path: str | Path) -> Path:
    """Write one row per equity snapshot or fill, tagged by ``record_type``."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for kind in ("equity", "fills"):
        for item in _get(result, kind, []) or []:
            row = _jsonable(item)
            rows.append({"record_type": "fill" if kind == "fills" else "equity", **row})
    columns = sorted({key for row in rows for key in row} | {"record_type"})
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted(columns), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return output
