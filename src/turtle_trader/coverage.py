"""Streaming exchange coverage accounting with sourced market-wide pauses.

Provider omissions cannot establish whether a slot had no trades, a halt, or
missing vendor data. Verified whole-bar halt intervals are reported separately;
other absent slots remain unknown. No candles are manufactured.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Literal
from zoneinfo import ZoneInfo

from turtle_trader.domain import Bar
from turtle_trader.halts import fully_halted

if TYPE_CHECKING:
    from turtle_trader.data import DataManifest, ExchangeCalendar

NY = ZoneInfo("America/New_York")
RangeClassification = Literal["unknown", "market_wide_halt"]


@dataclass(frozen=True)
class MissingRange:
    start: datetime
    end: datetime
    count: int
    classification: RangeClassification = "unknown"


@dataclass(frozen=True)
class SessionCoverage:
    day: date
    symbol: str
    expected_count: int
    observed_count: int
    missing_count: int
    missing_ranges: list[MissingRange]
    halted_count: int = 0
    halted_ranges: list[MissingRange] = field(default_factory=list)


@dataclass(frozen=True)
class CoverageReport:
    expected_count: int
    observed_count: int
    missing_count: int
    outside_count: int
    absent_symbols: list[str]
    sessions: list[SessionCoverage]
    halted_count: int = 0

    @property
    def complete(self) -> bool:
        return not (self.missing_count or self.outside_count or self.absent_symbols)


def _ordered_bars(bars: Iterable[Bar]) -> Iterator[Bar]:
    previous: tuple[datetime, str] | None = None
    for bar in bars:
        for at in (bar.time, bar.time_close):
            if at.tzinfo is None or at.utcoffset() is None:
                raise ValueError("coverage timestamps must have an explicit timezone")
        key = (bar.time.astimezone(UTC), bar.symbol)
        if previous is not None and key <= previous:
            raise ValueError("coverage bars must be unique and sorted by timestamp, symbol")
        previous = key
        yield bar


def _missing_ranges(
    expected: list[datetime], observed: set[datetime], delta: timedelta,
    *, classification: RangeClassification = "unknown",
) -> list[MissingRange]:
    result: list[MissingRange] = []
    start: datetime | None = None
    end: datetime | None = None
    count = 0
    for at in expected:
        if at not in observed:
            if start is not None and end is not None and at != end:
                result.append(MissingRange(start, end, count, classification))
                start, count = None, 0
            if start is None:
                start = at
            end = at + delta
            count += 1
        elif start is not None and end is not None:
            result.append(MissingRange(start, end, count, classification))
            start, count = None, 0
    if start is not None and end is not None:
        result.append(MissingRange(start, end, count, classification))
    return result


def coverage_report(
    bars: Iterable[Bar], manifest: "DataManifest", calendar: "ExchangeCalendar | None" = None,
) -> CoverageReport:
    """Report sorted bars using bounded, one-day observation sets.

    ``observed_count`` counts all input bars, including outside coverage.
    Per-session observed counts count eligible slots only. Missing ranges are
    half-open UTC intervals. Synthetic custom coverage has no inferred grid:
    its expected count equals eligible observed count and no gaps are inferred.
    Calendars opting into market halts exclude only fully halted bars; those
    exclusions are reported in separate counts and ranges. Partial halt-boundary
    bars remain required. Injected calendars retain their own trading schedule.
    """
    if calendar is None and manifest.coverage != "custom":
        from turtle_trader.data import ExchangeCalendar

        calendar = ExchangeCalendar(manifest.start_date, manifest.end_date)
    delta = timedelta(minutes=manifest.interval_minutes)
    iterator = iter(_ordered_bars(bars))
    current = next(iterator, None)
    symbols_seen: set[str] = set()
    observed_count = outside_count = expected_count = missing_count = halted_count = 0
    sessions: list[SessionCoverage] = []
    day = manifest.start_date
    while day <= manifest.end_date:
        expected: list[datetime] = []
        halted: list[datetime] = []
        if calendar is not None and manifest.coverage != "custom":
            bounds = calendar.bounds(day, manifest.coverage)
            if bounds is not None:
                at = bounds[0].astimezone(UTC)
                while at + delta <= bounds[1]:
                    if getattr(calendar, "market_halts", False) and fully_halted(at, delta):
                        halted.append(at)
                    else:
                        expected.append(at)
                    at += delta
        expected_set = set(expected)
        observed: dict[str, set[datetime]] = {}
        while current is not None and current.time.astimezone(NY).date() <= day:
            bar = current
            observed_count += 1
            symbols_seen.add(bar.symbol)
            at = bar.time.astimezone(UTC)
            local = at.astimezone(NY)
            eligible = (
                local.date() == day and bar.symbol in manifest.symbols
                and bar.time_close - bar.time == delta
                and not (at.second or at.microsecond)
                and local.minute % manifest.interval_minutes == 0
                and (manifest.coverage == "custom" or at in expected_set)
            )
            if eligible:
                observed.setdefault(bar.symbol, set()).add(at)
            else:
                outside_count += 1
            current = next(iterator, None)
        for symbol in manifest.symbols:
            symbol_observed = observed.get(symbol, set())
            if manifest.coverage == "custom":
                if not symbol_observed:
                    continue
                ranges: list[MissingRange] = []
                count = len(symbol_observed)
            else:
                if not expected:
                    continue
                ranges = _missing_ranges(expected, symbol_observed, delta)
                count = len(expected)
            missing = sum(value.count for value in ranges)
            halt_ranges = _missing_ranges(
                halted, set(), delta, classification="market_wide_halt",
            )
            sessions.append(SessionCoverage(
                day, symbol, count, len(symbol_observed), missing, ranges,
                len(halted), halt_ranges,
            ))
            expected_count += count
            missing_count += missing
            halted_count += len(halted)
        day += timedelta(days=1)
    while current is not None:
        observed_count += 1
        outside_count += 1
        symbols_seen.add(current.symbol)
        current = next(iterator, None)
    return CoverageReport(
        expected_count, observed_count, missing_count, outside_count,
        [symbol for symbol in manifest.symbols if symbol not in symbols_seen], sessions,
        halted_count,
    )
