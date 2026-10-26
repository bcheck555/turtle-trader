"""Offline dataset acquisition from Alpaca's fixed historical stock-bars endpoint.

The caller owns authentication and the injected client. No environment variables
or brokerage endpoints are accessed. Candle gaps remain gaps, including unknown
no-trade periods; adjusted data is explicitly labelled for research only.
"""

import csv
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import httpx

from .data import NY, DataManifest, ExchangeCalendar, coverage_report, validate_bar
from .domain import Bar
from .research import ADJUSTED_RESEARCH_WARNING

ENDPOINT = "https://data.alpaca.markets/v2/stocks/bars"
_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9./-]{0,19}\Z")
_TIMEOUT = httpx.Timeout(30, connect=10)


@dataclass(frozen=True)
class DownloadRequest:
    symbols: tuple[str, ...]
    start: date
    end: date
    interval_minutes: int = 5
    feed: str = "sip"
    coverage: str = "regular"

    def __post_init__(self) -> None:
        if (not isinstance(self.symbols, tuple) or not 1 <= len(self.symbols) <= 100
                or any(not isinstance(s, str) or _SYMBOL.fullmatch(s) is None
                       for s in self.symbols)
                or len(set(self.symbols)) != len(self.symbols)):
            raise ValueError("provide 1..100 unique uppercase stock symbols")
        if type(self.start) is not date or type(self.end) is not date or self.start > self.end:
            raise ValueError("start and end must be ordered dates")
        if type(self.interval_minutes) is not int or self.interval_minutes not in (
                1, 2, 3, 5, 6, 10, 15, 30):
            raise ValueError("unsupported minute interval")
        if self.feed not in ("sip", "iex"):
            raise ValueError("feed must be sip or iex")
        if self.coverage not in ("regular", "extended"):
            raise ValueError("coverage must be regular or extended")


@dataclass(frozen=True)
class DownloadResult:
    output: Path
    status: str
    exit_code: int
    pages: int = 0
    rows_received: int = 0
    rows_written: int = 0
    duplicates: int = 0
    excluded_out_of_hours: int = 0
    missing_count: int = 0
    error_code: str | None = None
    checksums: dict[str, str] = field(default_factory=dict)


class _DownloadError(Exception):
    """Only fixed, application-owned codes may cross the reporting boundary."""


@dataclass
class _Counts:
    pages: int = 0
    rows_received: int = 0
    rows_written: int = 0
    duplicates: int = 0
    excluded_out_of_hours: int = 0
    retries: int = 0


def _utc(at: datetime) -> str:
    return at.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json_default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError("unsupported report value")


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=_json_default)
                    + "\n", encoding="utf-8")


def _checksum(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _version(distribution: str) -> str:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return "unknown"


def _retry_delay(response: httpx.Response, attempt: int, now: datetime) -> float:
    delay = float(2 ** attempt)
    raw = response.headers.get("Retry-After")
    if raw is None:
        return delay
    try:
        seconds = float(raw)
        if not math.isfinite(seconds):
            raise ValueError("invalid delay")
    except ValueError:
        try:
            at = parsedate_to_datetime(raw)
            if at.tzinfo is None:
                at = at.replace(tzinfo=UTC)
            seconds = max(0.0, (at - now).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return delay
    if seconds > 60:
        raise _DownloadError("retry_after_exceeds_60_seconds")
    return max(delay, seconds)


def _page(client: httpx.Client, params: dict[str, str], now: Callable[[], datetime],
          sleep: Callable[[float], None], counts: _Counts) -> dict[str, Any]:
    for attempt in range(4):
        try:
            response = client.get(ENDPOINT, params=params, timeout=_TIMEOUT,
                                  follow_redirects=False)
        except httpx.TransportError:
            if attempt == 3:
                raise _DownloadError("network_retries_exhausted") from None
            counts.retries += 1
            sleep(float(2 ** attempt))
            continue
        if response.status_code == 429 or 500 <= response.status_code <= 599:
            if attempt == 3:
                raise _DownloadError("http_retries_exhausted")
            delay = _retry_delay(response, attempt, now())
            counts.retries += 1
            sleep(delay)
            continue
        if response.status_code != 200:
            raise _DownloadError(f"http_status_{response.status_code}")
        try:
            payload = response.json(parse_float=Decimal)
        except (ValueError, TypeError):
            raise _DownloadError("invalid_json") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("bars"), dict):
            raise _DownloadError("invalid_page_schema")
        counts.pages += 1
        return payload
    raise _DownloadError("network_retries_exhausted")


def _number(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise _DownloadError("invalid_bar_price")
    try:
        number = Decimal(str(value))
        converted = float(number)
        if (not number.is_finite() or number <= 0 or not math.isfinite(converted)
                or converted <= 0):
            raise _DownloadError("invalid_bar_price")
        canonical = format(number, "f")
        return canonical.rstrip("0").rstrip(".") if "." in canonical else canonical
    except (InvalidOperation, ValueError, OverflowError):
        raise _DownloadError("invalid_bar_price") from None


def _parse_bar(symbol: str, raw: object, request: DownloadRequest) -> tuple[Bar, tuple[str, ...]]:
    if not isinstance(raw, dict) or not all(k in raw for k in ("t", "o", "h", "l", "c", "v")):
        raise _DownloadError("invalid_bar_schema")
    try:
        if not isinstance(raw["t"], str):
            raise ValueError("invalid timestamp")
        at = datetime.fromisoformat(raw["t"].replace("Z", "+00:00"))
        if at.utcoffset() is None:
            raise ValueError("naive timestamp")
        at = at.astimezone(UTC)
        if (at.second or at.microsecond or at.astimezone(NY).minute % request.interval_minutes
                or not request.start <= at.astimezone(NY).date() <= request.end):
            raise ValueError("unaligned or outside request")
    except (ValueError, OverflowError):
        raise _DownloadError("invalid_bar_timestamp") from None
    prices = tuple(_number(raw[k]) for k in ("o", "h", "l", "c"))
    opening, high, low, close = (Decimal(p) for p in prices)
    if not low <= min(opening, close) <= max(opening, close) <= high:
        raise _DownloadError("invalid_bar_values")
    volume_raw = raw["v"]
    if isinstance(volume_raw, bool) or not isinstance(volume_raw, (int, float, Decimal)):
        raise _DownloadError("invalid_bar_volume")
    try:
        volume = Decimal(str(volume_raw))
        if (not volume.is_finite() or volume < 0 or volume != volume.to_integral_value()
                or volume > 2 ** 63 - 1):
            raise _DownloadError("invalid_bar_volume")
        bar = Bar(symbol, at, at + timedelta(minutes=request.interval_minutes),
                  float(prices[0]), float(prices[1]), float(prices[2]), float(prices[3]),
                  int(volume))
        validate_bar(bar)
    except (ValueError, OverflowError, InvalidOperation):
        raise _DownloadError("invalid_bar_values") from None
    return bar, prices


def _bars(database: sqlite3.Connection, interval: int) -> Iterator[Bar]:
    for symbol, timestamp, opening, high, low, close, volume in database.execute(
            "SELECT symbol, timestamp, o, h, l, c, v FROM bars "
            "WHERE eligible = 1 ORDER BY timestamp, symbol"):
        at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        yield Bar(symbol, at, at + timedelta(minutes=interval),
                  float(opening), float(high), float(low), float(close), volume)


def _acquire(request: DownloadRequest, client: httpx.Client, now: Callable[[], datetime],
             sleep: Callable[[float], None], counts: _Counts,
             database: sqlite3.Connection, calendar: ExchangeCalendar,
             start: datetime, end: datetime) -> None:
    database.execute("CREATE TABLE bars (symbol TEXT, timestamp TEXT, o TEXT, h TEXT, "
                     "l TEXT, c TEXT, v INTEGER, eligible INTEGER, "
                     "PRIMARY KEY (symbol, timestamp))")
    symbols = sorted(request.symbols)
    for offset in range(0, len(symbols), 50):
        batch = symbols[offset:offset + 50]
        params = {"symbols": ",".join(batch), "timeframe": f"{request.interval_minutes}Min",
                  "start": _utc(start), "end": _utc(end), "limit": "10000", "sort": "asc",
                  "adjustment": "all", "asof": request.end.isoformat(), "feed": request.feed}
        tokens: set[str] = set()
        while True:
            payload = _page(client, params, now, sleep, counts)
            for symbol, rows in payload["bars"].items():
                if symbol not in batch or not isinstance(rows, list):
                    raise _DownloadError("invalid_page_symbols")
                for raw in rows:
                    counts.rows_received += 1
                    bar, prices = _parse_bar(symbol, raw, request)
                    bounds = calendar.bounds(bar.time.astimezone(NY).date(), request.coverage)
                    eligible = int(bounds is not None and bounds[0] <= bar.time
                                   and bar.time_close <= bounds[1])
                    row = (symbol, _utc(bar.time), *prices, bar.volume, eligible)
                    existing = database.execute(
                        "SELECT symbol, timestamp, o, h, l, c, v, eligible FROM bars "
                        "WHERE symbol = ? AND timestamp = ?", row[:2]).fetchone()
                    if existing is not None:
                        if tuple(existing) != row:
                            raise _DownloadError("conflicting_duplicate")
                        counts.duplicates += 1
                    else:
                        database.execute("INSERT INTO bars VALUES (?, ?, ?, ?, ?, ?, ?, ?)", row)
                        if eligible:
                            counts.rows_written += 1
                        else:
                            counts.excluded_out_of_hours += 1
            token = payload.get("next_page_token")
            if token is None:
                break
            if not isinstance(token, str) or not token or len(token) > 4096:
                raise _DownloadError("invalid_page_token")
            if token in tokens:
                raise _DownloadError("repeated_page_token")
            tokens.add(token)
            params["page_token"] = token
    database.commit()


def _manifest(request: DownloadRequest) -> str:
    return ("[data]\n"
            f"feed = {json.dumps(request.feed)}\n"
            f"interval_minutes = {request.interval_minutes}\n"
            f"coverage = {json.dumps(request.coverage)}\n"
            'adjustment = "all"\naction_free = false\n'
            'price_basis = "adjusted_research"\n'
            f"start_date = {request.start.isoformat()}\nend_date = {request.end.isoformat()}\n"
            f"symbols = {json.dumps(sorted(request.symbols))}\n"
            'timestamp_convention = "bar_open"\nsynthetic = false\n')


def _publish(stage: Path, output: Path) -> None:
    # mkdir exclusively claims the destination, so a racing writer cannot have
    # an existing empty directory silently replaced by POSIX rename semantics.
    output.mkdir()
    try:
        os.replace(stage, output)
    except BaseException:
        output.rmdir()
        raise


def download_alpaca(request: DownloadRequest, output: Path, *, client: httpx.Client,
                    now: datetime | None = None,
                    sleep: Callable[[float], None] = time.sleep,
                    monotonic: Callable[[], float] = time.monotonic) -> DownloadResult:
    """Acquire all pages, then publish four artifacts or a sanitized error only.

    Existing destinations are always refused. Successful publication atomically
    replaces an exclusively claimed empty destination with the staging directory.
    Failure codes contain no HTTP bodies, headers, page tokens, or exception text.
    """
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise FileExistsError("download destination already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent))
    counts = _Counts()
    acquisition_time = now if now is not None else datetime.now(UTC)
    try:
        if acquisition_time.utcoffset() is None:
            raise _DownloadError("naive_acquisition_time")
        started = monotonic()

        def retry_now() -> datetime:
            return acquisition_time + timedelta(seconds=max(0, monotonic() - started))

        if request.end >= acquisition_time.astimezone(NY).date():
            raise _DownloadError("dates_must_precede_current_new_york_date")
        calendar = ExchangeCalendar(request.start, request.end)
        first: datetime | None = None
        last: datetime | None = None
        day = request.start
        while day <= request.end:
            bounds = calendar.bounds(day, request.coverage)
            if bounds is not None:
                first = first if first is not None else bounds[0]
                last = bounds[1]
            day += timedelta(days=1)
        if first is None or last is None:
            raise _DownloadError("no_exchange_sessions")
        if request.feed == "sip" and last > acquisition_time - timedelta(minutes=16):
            raise _DownloadError("sip_end_must_be_at_least_16_minutes_old")
        manifest = DataManifest(
            feed=request.feed, interval_minutes=request.interval_minutes, coverage=request.coverage,
            adjustment="all", action_free=False, start_date=request.start, end_date=request.end,
            symbols=tuple(sorted(request.symbols)), price_basis="adjusted_research")
        db_path = stage / "staging.sqlite"
        database = sqlite3.connect(db_path)
        try:
            _acquire(request, client, retry_now, sleep, counts, database, calendar,
                     first, last)
            report = coverage_report(_bars(database, request.interval_minutes), manifest, calendar)
            with (stage / "bars.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, lineterminator="\n")
                writer.writerow(("symbol", "timestamp", "open", "high", "low", "close", "volume"))
                writer.writerows(database.execute(
                    "SELECT symbol, timestamp, o, h, l, c, v FROM bars "
                    "WHERE eligible = 1 ORDER BY timestamp, symbol"))
        finally:
            database.close()
        db_path.unlink()
        (stage / "manifest.toml").write_text(_manifest(request), encoding="utf-8")
        _write_json(stage / "coverage.json", asdict(report) | {
            "complete": report.complete,
            "excluded_out_of_hours": counts.excluded_out_of_hours})
        checksums = {name: _checksum(stage / name)
                     for name in ("bars.csv", "manifest.toml", "coverage.json")}
        status = "ready_for_adjusted_research" if report.complete else "coverage_incomplete"
        _write_json(stage / "provenance.json", {
            "schema_version": 1, "status": status, "request": asdict(request),
            "query_parameters": {
                "timeframe": f"{request.interval_minutes}Min", "asof": request.end.isoformat(),
                "adjustment": "all", "limit": 10000, "sort": "asc", "feed": request.feed},
            "acquired_at": acquisition_time, "query_start": first, "query_end": last,
            "endpoint": ENDPOINT, "counts": asdict(counts), "artifact_checksums": checksums,
            "versions": {"engine": _version("turtle-trader"),
                         "calendar": _version("exchange-calendars"), "httpx": httpx.__version__},
            "price_basis": "adjusted_research", "warnings": [ADJUSTED_RESEARCH_WARNING],
            "exclusion_policy": "unique bars outside complete declared exchange intervals",
            "gap_classification": "unknown; no gaps filled",
        })
        checksums["provenance.json"] = _checksum(stage / "provenance.json")
        _publish(stage, output)
        return DownloadResult(output, status, 0 if report.complete else 3, counts.pages,
                              counts.rows_received, counts.rows_written, counts.duplicates,
                              counts.excluded_out_of_hours, report.missing_count,
                              checksums=checksums)
    except FileExistsError:
        raise
    except (Exception, KeyboardInterrupt) as error:
        code = str(error) if isinstance(error, _DownloadError) else "download_failed"
        shutil.rmtree(stage)
        stage.mkdir()
        _write_json(stage / "error.json", {
            "status": "failed", "exit_code": 2, "error_code": code,
            "counts": asdict(counts),
        })
        _publish(stage, output)
        return DownloadResult(output, "failed", 2, counts.pages, counts.rows_received,
                              duplicates=counts.duplicates, error_code=code)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
