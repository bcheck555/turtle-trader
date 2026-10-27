import csv
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from turtle_trader.coverage import coverage_report
from turtle_trader.data import DataManifest, load_dataset
from turtle_trader.domain import Bar

DAY = date(2020, 3, 9)
OPEN = datetime(2020, 3, 9, 13, 30, tzinfo=UTC)
FIRST_HALTED_MINUTE = OPEN + timedelta(minutes=5)
FIRST_RESTART_MINUTE = OPEN + timedelta(minutes=19)


def manifest(minutes: int = 1) -> DataManifest:
    return DataManifest(
        feed="fixture", interval_minutes=minutes, coverage="regular", adjustment="all",
        action_free=False, price_basis="adjusted_research", start_date=DAY, end_date=DAY,
        symbols=("QQQ",),
    )


def candle(at: datetime, minutes: int = 1) -> Bar:
    return Bar("QQQ", at, at + timedelta(minutes=minutes), 10, 11, 9, 10, 100)


def tradable_bars(minutes: int = 1) -> list[Bar]:
    delta = timedelta(minutes=minutes)
    # The sourced halt is 13:34:13–13:49:13 UTC. Only whole candles within it
    # disappear: partial halt-start and restart candles still have trades.
    return [
        candle(at, minutes) for n in range(390 // minutes)
        if not (FIRST_HALTED_MINUTE <= (at := OPEN + n * delta)
                and at + delta <= FIRST_RESTART_MINUTE)
    ]


def write_dataset(tmp_path: Path, bars: list[Bar]) -> tuple[Path, Path]:
    csv_path = tmp_path / "bars.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("symbol", "timestamp", "open", "high", "low", "close", "volume"))
        writer.writerows((b.symbol, b.time.isoformat(), b.open, b.high, b.low, b.close, b.volume)
                         for b in bars)
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(
        '[data]\nfeed="fixture"\ninterval_minutes=1\ncoverage="regular"\n'
        'adjustment="all"\naction_free=false\nprice_basis="adjusted_research"\n'
        'start_date="2020-03-09"\nend_date="2020-03-09"\nsymbols=["QQQ"]\n',
        encoding="utf-8",
    )
    return csv_path, manifest_path


def test_halt_exclusions_are_complete_and_separately_disclosed() -> None:
    report = coverage_report(tradable_bars(), manifest())
    assert report.complete
    assert report.expected_count == report.observed_count == 376
    assert report.missing_count == report.outside_count == 0
    assert report.halted_count == 14
    session = report.sessions[0]
    assert session.halted_count == 14 and session.missing_ranges == []
    assert [(r.start, r.end, r.count, r.classification) for r in session.halted_ranges] == [
        (FIRST_HALTED_MINUTE, FIRST_RESTART_MINUTE, 14, "market_wide_halt"),
    ]


@pytest.mark.parametrize("minute", [4, 19])
def test_partial_halt_boundary_candles_are_still_required(minute: int) -> None:
    omitted = OPEN + timedelta(minutes=minute)
    report = coverage_report((b for b in tradable_bars() if b.time != omitted), manifest())
    assert not report.complete
    assert report.missing_count == 1 and report.halted_count == 14
    assert report.sessions[0].missing_ranges[0].classification == "unknown"


def test_missing_ranges_do_not_bridge_verified_halt() -> None:
    omitted = {OPEN + timedelta(minutes=4), FIRST_RESTART_MINUTE}
    report = coverage_report((b for b in tradable_bars() if b.time not in omitted), manifest())
    assert report.missing_count == 2
    assert [(r.start, r.end, r.count) for r in report.sessions[0].missing_ranges] == [
        (OPEN + timedelta(minutes=4), FIRST_HALTED_MINUTE, 1),
        (FIRST_RESTART_MINUTE, FIRST_RESTART_MINUTE + timedelta(minutes=1), 1),
    ]


def test_five_minute_bars_exclude_only_wholly_halted_intervals() -> None:
    report = coverage_report(tradable_bars(5), manifest(5))
    assert report.complete and report.halted_count == 2
    assert report.expected_count == report.observed_count == 76
    assert report.sessions[0].halted_ranges[0].start == FIRST_HALTED_MINUTE
    assert report.sessions[0].halted_ranges[0].end == OPEN + timedelta(minutes=15)


def test_custom_synthetic_coverage_is_not_filtered_by_real_halts() -> None:
    bars = [candle(OPEN + timedelta(minutes=n)) for n in range(390)]
    custom = replace(manifest(), coverage="custom", synthetic=True)
    report = coverage_report(bars, custom)
    assert report.complete and report.expected_count == 390
    assert report.halted_count == 0
    assert report.sessions[0].halted_ranges == []


@pytest.mark.parametrize("include_halt_bars", [False, True])
def test_injected_calendar_must_opt_into_market_halts(include_halt_bars: bool) -> None:
    class FixedCalendar:
        def bounds(self, day: date, market_hours: str) -> tuple[datetime, datetime] | None:
            return (OPEN, OPEN + timedelta(minutes=390)) if day == DAY else None

    bars = ([candle(OPEN + timedelta(minutes=n)) for n in range(390)]
            if include_halt_bars else tradable_bars())
    report = coverage_report(bars, manifest(), FixedCalendar())  # type: ignore[arg-type]
    assert report.expected_count == 390 and report.halted_count == 0
    assert report.outside_count == 0
    assert report.missing_count == (0 if include_halt_bars else 14)
    assert report.complete == include_halt_bars


def test_observed_candle_wholly_inside_halt_is_outside_coverage(tmp_path: Path) -> None:
    bars = sorted(tradable_bars() + [candle(FIRST_HALTED_MINUTE)], key=lambda b: b.time)
    report = coverage_report(bars, manifest())
    assert not report.complete and report.outside_count == 1
    assert report.missing_count == 0 and report.halted_count == 14
    csv_path, manifest_path = write_dataset(tmp_path, bars)
    with pytest.raises(ValueError, match="outside declared exchange coverage"):
        load_dataset(csv_path, manifest_path)


def test_strict_import_accepts_verified_halt_without_fabrication(tmp_path: Path) -> None:
    bars = tradable_bars()
    csv_path, manifest_path = write_dataset(tmp_path, bars)
    dataset = load_dataset(csv_path, manifest_path)
    assert dataset.bars == bars
    assert len(dataset.bars) == 376


def test_strict_import_still_rejects_unknown_missing_candle(tmp_path: Path) -> None:
    bars = [b for b in tradable_bars() if b.time != FIRST_RESTART_MINUTE]
    csv_path, manifest_path = write_dataset(tmp_path, bars)
    with pytest.raises(ValueError, match="1 missing candles"):
        load_dataset(csv_path, manifest_path)
