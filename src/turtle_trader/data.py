"""Offline OHLCV import, explicit coverage manifests and exchange-calendar validation."""

import csv
import hashlib
import math
import tomllib
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

from turtle_trader.domain import Bar

NY = ZoneInfo("America/New_York")


class Calendar(Protocol):
    def eligible(self, at: datetime, market_hours: str = "regular") -> bool: ...


class HistoricalProvider(Protocol):
    def bars(self, symbol: str, start: datetime, end: datetime, interval: int) -> list[Bar]: ...


class ExchangeCalendar:
    """XNYS holidays/early closes; extended early-close days end conservatively at close."""

    def __init__(self, start: date | None = None, end: date | None = None) -> None:
        self._calendar = xcals.get_calendar(
            "XNYS", start=str((start or date(1990, 1, 1)) - timedelta(days=7)),
            end=str((end or date(2040, 12, 31)) + timedelta(days=7))
        )

    def bounds(self, day: date, market_hours: str) -> tuple[datetime, datetime] | None:
        if market_hours not in ("regular", "extended"):
            raise ValueError("market_hours must be regular or extended")
        if not self._calendar.is_session(str(day)):
            return None
        opening = self._calendar.session_open(str(day)).to_pydatetime()
        closing = self._calendar.session_close(str(day)).to_pydatetime()
        if market_hours == "extended":
            opening = datetime.combine(day, time(4), NY).astimezone(UTC)
            if closing.astimezone(NY).time() >= time(16):
                closing = datetime.combine(day, time(20), NY).astimezone(UTC)
        return opening, closing

    def eligible(self, at: datetime, market_hours: str = "regular") -> bool:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("timestamps must have an explicit timezone")
        bounds = self.bounds(at.astimezone(NY).date(), market_hours)
        return bounds is not None and bounds[0] <= at < bounds[1]


@dataclass(frozen=True)
class DataManifest:
    feed: str
    interval_minutes: int
    coverage: str
    adjustment: str
    action_free: bool
    start_date: date
    end_date: date
    symbols: tuple[str, ...]
    timestamp_convention: str = "bar_open"
    synthetic: bool = False

    def __post_init__(self) -> None:
        if not self.feed or not self.symbols or len(set(self.symbols)) != len(self.symbols):
            raise ValueError("feed and unique symbols required")
        if self.interval_minutes not in (1, 2, 3, 5, 6, 10, 15, 30):
            raise ValueError("unsupported interval")
        if self.coverage not in ("regular", "extended", "custom"):
            raise ValueError("unsupported coverage")
        if self.coverage == "custom" and not self.synthetic:
            raise ValueError("custom coverage requires explicitly synthetic data")
        if self.adjustment not in ("split", "all") or self.action_free is not True:
            raise ValueError("milestone requires consistently adjusted, action-free data")
        if self.end_date < self.start_date or self.timestamp_convention != "bar_open":
            raise ValueError("invalid dates or timestamp convention")
        if type(self.synthetic) is not bool:
            raise ValueError("synthetic must be boolean")


@dataclass
class Dataset:
    bars: list[Bar]
    manifest: DataManifest
    data_hash: str
    warnings: list[str]


def read_manifest(path: Path) -> DataManifest:
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    if set(raw) != {"data"} or not isinstance(raw["data"], dict):
        raise ValueError("manifest must contain one [data] table")
    values = raw["data"]
    allowed = set(DataManifest.__dataclass_fields__)
    if set(values) - allowed:
        raise ValueError(f"unknown manifest keys: {sorted(set(values) - allowed)}")
    for key in ("start_date", "end_date"):
        value = values.get(key)
        if isinstance(value, str):
            values[key] = date.fromisoformat(value)
        elif type(value) is not date:
            raise ValueError(f"{key} must be an ISO date")
    symbols = values.get("symbols")
    if not isinstance(symbols, list) or not all(isinstance(s, str) and s for s in symbols):
        raise ValueError("symbols must be a nonempty string array")
    values["symbols"] = tuple(symbols)
    return DataManifest(**values)


def _timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timestamps must include UTC offset")
    return result.astimezone(UTC)


def validate_bar(bar: Bar) -> None:
    if any(t.tzinfo is None or t.utcoffset() is None for t in (bar.time, bar.time_close)):
        raise ValueError("naive bar timestamp")
    prices = (bar.open, bar.high, bar.low, bar.close)
    if not all(math.isfinite(p) and p > 0 for p in prices):
        raise ValueError("prices must be finite and positive")
    if not bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high:
        raise ValueError("invalid OHLC bounds")
    if bar.volume < 0 or bar.time_close <= bar.time or not bar.symbol:
        raise ValueError("invalid bar volume, symbol or interval")


def load_dataset(csv_path: Path, manifest_path: Path, *, strict: bool = True) -> Dataset:
    manifest = read_manifest(manifest_path)
    delta = timedelta(minutes=manifest.interval_minutes)
    bars: list[Bar] = []
    seen: set[tuple[str, datetime]] = set()
    last: dict[str, datetime] = {}
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"symbol", "timestamp", "open", "high", "low", "close", "volume"}
        if reader.fieldnames is None or set(reader.fieldnames) != required:
            raise ValueError("CSV columns must be symbol,timestamp,open,high,low,close,volume")
        for number, row in enumerate(reader, 2):
            try:
                at = _timestamp(row["timestamp"])
                bar = Bar(row["symbol"], at, at + delta, float(row["open"]),
                          float(row["high"]), float(row["low"]), float(row["close"]),
                          int(row["volume"]))
                validate_bar(bar)
                if bar.symbol not in manifest.symbols:
                    raise ValueError("undeclared symbol")
                if not manifest.start_date <= at.astimezone(NY).date() <= manifest.end_date:
                    raise ValueError("timestamp outside declared date coverage")
                if (at.second or at.microsecond
                        or at.astimezone(NY).minute % manifest.interval_minutes):
                    raise ValueError("bar must align to interval boundary")
                key = (bar.symbol, at)
                if key in seen or (bar.symbol in last and at <= last[bar.symbol]):
                    raise ValueError("duplicate or nonchronological symbol bars")
                seen.add(key)
                last[bar.symbol] = at
                bars.append(bar)
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError(f"CSV row {number}: {exc}") from exc
    if not bars:
        raise ValueError("dataset is empty")
    warnings: list[str] = []
    if manifest.coverage == "custom":
        warnings.append("Synthetic custom coverage: not a real-market performance dataset")
    else:
        calendar = ExchangeCalendar(manifest.start_date, manifest.end_date)
        missing = 0
        expected: set[tuple[str, datetime]] = set()
        day = manifest.start_date
        while day <= manifest.end_date:
            bounds = calendar.bounds(day, manifest.coverage)
            if bounds is not None:
                at = bounds[0]
                while at + delta <= bounds[1]:
                    for symbol in manifest.symbols:
                        key = (symbol, at)
                        expected.add(key)
                        missing += key not in seen
                    at += delta
            day += timedelta(days=1)
        outside = len(seen - expected)
        if outside:
            raise ValueError(f"{outside} candles outside declared exchange coverage")
        if missing:
            message = f"{missing} missing candles in declared coverage; no forward fill"
            if strict:
                raise ValueError(message)
            warnings.append(message)
    digest = hashlib.sha256()
    digest.update(csv_path.read_bytes())
    digest.update(b"\0")
    digest.update(manifest_path.read_bytes())
    return Dataset(sorted(bars, key=lambda b: (b.time, b.symbol)), manifest,
                   digest.hexdigest(), warnings)


class CSVProvider:
    def __init__(self, dataset: Dataset) -> None:
        self.dataset = dataset

    def bars(self, symbol: str, start: datetime, end: datetime, interval: int) -> list[Bar]:
        if interval != self.dataset.manifest.interval_minutes:
            raise ValueError("resampling unsupported; import requested interval explicitly")
        return [b for b in self.dataset.bars if b.symbol == symbol and start <= b.time < end]
