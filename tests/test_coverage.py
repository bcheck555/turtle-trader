from collections.abc import Iterator
from dataclasses import asdict, replace
from datetime import UTC, date, datetime, timedelta

import pytest

from turtle_trader.data import DataManifest, ExchangeCalendar, coverage_report
from turtle_trader.domain import Bar


def manifest(**kwargs: object) -> DataManifest:
    values: dict[str, object] = {
        "feed": "fixture", "interval_minutes": 5, "coverage": "regular",
        "adjustment": "all", "action_free": False, "price_basis": "adjusted_research",
        "start_date": date(2026, 1, 5), "end_date": date(2026, 1, 5),
        "symbols": ("ABC",),
    }
    values.update(kwargs)
    return DataManifest(**values)  # type: ignore[arg-type]


def candle(at: datetime, symbol: str = "ABC", minutes: int = 5) -> Bar:
    return Bar(symbol, at, at + timedelta(minutes=minutes), 10, 11, 9, 10, 100)


def regular_bars(day: date, *, symbols: tuple[str, ...] = ("ABC",)) -> list[Bar]:
    bounds = ExchangeCalendar(day, day).bounds(day, "regular")
    if bounds is None:
        return []
    opening, closing = bounds
    result: list[Bar] = []
    at = opening
    while at < closing:
        result.extend(candle(at, symbol) for symbol in sorted(symbols))
        at += timedelta(minutes=5)
    return result


def test_complete_generator_and_report_schema() -> None:
    bars = regular_bars(date(2026, 1, 5), symbols=("ABC", "DEF"))
    report = coverage_report(iter(bars), manifest(symbols=("ABC", "DEF")))
    assert report.complete
    assert (report.expected_count, report.observed_count, report.missing_count) == (156, 156, 0)
    assert report.outside_count == 0 and report.absent_symbols == []
    assert len(report.sessions) == 2
    assert asdict(report.sessions[0]) == {
        "day": date(2026, 1, 5), "symbol": "ABC", "expected_count": 78,
        "observed_count": 78, "missing_count": 0, "missing_ranges": [],
    }


def test_halfopen_missing_ranges_never_claim_no_trades() -> None:
    bars = regular_bars(date(2026, 1, 5))
    removed = {0, 1, 5, 77}
    report = coverage_report((bar for n, bar in enumerate(bars) if n not in removed), manifest())
    assert not report.complete and report.missing_count == 4
    ranges = report.sessions[0].missing_ranges
    assert [(r.start, r.end, r.count) for r in ranges] == [
        (bars[0].time, bars[2].time, 2),
        (bars[5].time, bars[6].time, 1),
        (bars[77].time, bars[77].time_close, 1),
    ]
    assert all(r.classification == "unknown" for r in ranges)


def test_empty_download_and_absent_symbols_account_all_slots() -> None:
    report = coverage_report([], manifest(symbols=("ABC", "DEF")))
    assert not report.complete and report.absent_symbols == ["ABC", "DEF"]
    assert report.expected_count == report.missing_count == 156
    assert report.observed_count == 0
    assert all(s.missing_ranges[0].count == 78 for s in report.sessions)
    report = coverage_report(regular_bars(date(2026, 1, 5)), manifest(symbols=("ABC", "DEF")))
    assert report.absent_symbols == ["DEF"] and report.missing_count == 78


def test_holiday_weekend_early_close_and_extended() -> None:
    for day in (date(2026, 12, 25), date(2026, 1, 10)):
        report = coverage_report([], manifest(start_date=day, end_date=day))
        assert report.expected_count == report.missing_count == 0
        assert report.sessions == []
        assert report.absent_symbols == ["ABC"] and not report.complete
    day = date(2026, 11, 27)
    regular = coverage_report(regular_bars(day), manifest(start_date=day, end_date=day))
    assert regular.complete and regular.expected_count == 42
    extended = coverage_report([], manifest(start_date=day, end_date=day, coverage="extended"))
    assert extended.expected_count == 108  # 04:00 through conservative 13:00 close.
    ordinary = coverage_report([], manifest(coverage="extended"))
    assert ordinary.expected_count == 192


def test_dst_changes_utc_grid_without_changing_session_size() -> None:
    first, last = date(2026, 3, 6), date(2026, 3, 9)
    bars = regular_bars(first) + regular_bars(last)
    report = coverage_report(bars, manifest(start_date=first, end_date=last))
    assert report.complete and report.expected_count == 156
    assert [s.day for s in report.sessions] == [first, last]
    assert bars[0].time.hour == 14 and bars[78].time.hour == 13


def test_outside_hours_unknown_symbols_and_dates_remain_visible() -> None:
    day = date(2026, 1, 5)
    bars = regular_bars(day)
    bars += [
        candle(datetime(2026, 1, 4, 15, tzinfo=UTC)),
        candle(datetime(2026, 1, 5, 14, tzinfo=UTC)),
        candle(datetime(2026, 1, 5, 14, 30, tzinfo=UTC), "OTHER"),
        candle(datetime(2026, 1, 6, 15, tzinfo=UTC)),
    ]
    report = coverage_report(sorted(bars, key=lambda b: (b.time, b.symbol)), manifest())
    assert report.expected_count == 78 and report.observed_count == 82
    assert report.missing_count == 0 and report.outside_count == 4 and not report.complete


def test_offgrid_or_wrong_interval_is_outside_and_leaves_gap() -> None:
    bars = regular_bars(date(2026, 1, 5))
    bars[0] = candle(bars[0].time + timedelta(minutes=1))
    bars[1] = candle(bars[1].time, minutes=10)
    report = coverage_report(bars, manifest())
    assert report.outside_count == 2 and report.missing_count == 2
    assert report.sessions[0].observed_count == 76


@pytest.mark.parametrize("mode", ["duplicate", "reverse", "naive"])
def test_invalid_stream_fails_safe(mode: str) -> None:
    bars = regular_bars(date(2026, 1, 5))[:2]
    if mode == "duplicate":
        bars[1] = bars[0]
    elif mode == "reverse":
        bars.reverse()
    else:
        bars[0] = replace(bars[0], time=bars[0].time.replace(tzinfo=None))
    with pytest.raises(ValueError):
        coverage_report(bars, manifest())


def test_custom_synthetic_has_no_inferred_grid() -> None:
    bars = [candle(datetime(2026, 1, 5, 2, tzinfo=UTC)),
            candle(datetime(2026, 1, 5, 15, tzinfo=UTC))]
    # First UTC date belongs to previous NY date and is outside declared dates.
    report = coverage_report(bars, manifest(coverage="custom", synthetic=True))
    assert report.expected_count == 1 and report.observed_count == 2
    assert report.outside_count == 1 and report.missing_count == 0
    assert report.sessions[0].missing_ranges == []


def test_calendar_bounds_reused_across_symbols_and_input_is_single_pass() -> None:
    class CountingCalendar(ExchangeCalendar):
        def __init__(self) -> None:
            super().__init__(date(2026, 1, 5), date(2026, 1, 6))
            self.calls = 0

        def bounds(self, day: date, market_hours: str) -> tuple[datetime, datetime] | None:
            self.calls += 1
            return super().bounds(day, market_hours)

    class Once:
        def __init__(self, bars: list[Bar]) -> None:
            self.bars = bars
            self.iterated = False

        def __iter__(self) -> Iterator[Bar]:
            assert not self.iterated
            self.iterated = True
            yield from self.bars

    calendar = CountingCalendar()
    report = coverage_report(Once(regular_bars(date(2026, 1, 5))),
                             manifest(symbols=("ABC", "DEF"), end_date=date(2026, 1, 6)),
                             calendar)
    assert calendar.calls == 2 and len(report.sessions) == 4
    assert report.expected_count == 312 and report.missing_count == 234


def test_complete_multiday_multisymbol_generator() -> None:
    first, last = date(2026, 3, 6), date(2026, 3, 9)
    symbols = ("ZZZ", "ABC")

    def stream() -> Iterator[Bar]:
        for day in (first, last):
            yield from regular_bars(day, symbols=symbols)

    report = coverage_report(stream(), manifest(start_date=first, end_date=last, symbols=symbols))
    assert report.complete
    assert report.expected_count == report.observed_count == 312
    assert report.missing_count == report.outside_count == 0
    assert [(s.day, s.symbol) for s in report.sessions] == [
        (first, "ZZZ"), (first, "ABC"), (last, "ZZZ"), (last, "ABC"),
    ]


def test_zero_volume_observed_bar_is_present_without_inferring_omitted_slots() -> None:
    bars = regular_bars(date(2026, 1, 5))
    bars[0] = replace(bars[0], volume=0)
    report = coverage_report(bars, manifest())
    assert report.complete and report.observed_count == 78
