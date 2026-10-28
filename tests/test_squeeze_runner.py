"""Data-boundary and accounting regressions for the cached Squeeze experiment."""

import csv
import importlib.util
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from turtle_trader.data import DataManifest, ExchangeCalendar, read_manifest
from turtle_trader.domain import ExecutionConfig
from turtle_trader.halts import fully_halted
from turtle_trader.reporting import summarize_result

RUNNER = Path(__file__).resolve().parents[1] / "research/run_squeeze_comparison.py"
SPEC = importlib.util.spec_from_file_location("squeeze_runner", RUNNER)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class Schedule:
    market_halts = True

    def __init__(self, days: list[date], minutes: int = 390) -> None:
        self.sessions = {
            day: (datetime(day.year, day.month, day.day, 13, 30, tzinfo=UTC),
                  datetime(day.year, day.month, day.day, 13, 30, tzinfo=UTC)
                  + timedelta(minutes=minutes))
            for day in days
        }

    def bounds(self, day: date, market_hours: str) -> tuple[datetime, datetime] | None:
        return self.sessions.get(day)

    def eligible(self, at: datetime, market_hours: str = "regular") -> bool:
        bounds = self.bounds(at.date(), market_hours)
        return bounds is not None and bounds[0] <= at < bounds[1]


def dataset(tmp_path: Path, schedule: Schedule,
            omit: set[datetime] | None = None) -> tuple[Path, DataManifest]:
    path = tmp_path / "minutes.csv"
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("symbol", "timestamp", "open", "high", "low", "close", "volume"))
        for opening, closing in schedule.sessions.values():
            at = opening
            while at < closing:
                if at not in (omit or set()) and not fully_halted(at, timedelta(minutes=1)):
                    # Zero-volume candles remain valid; missing is not synonymous with zero.
                    writer.writerow(("QQQ", at.isoformat(), 100, 102, 99, 101, 0))
                at += timedelta(minutes=1)
    days = sorted(schedule.sessions)
    manifest = DataManifest("fixture", 1, "regular", "all", False,
                            days[0], days[-1], ("QQQ",), synthetic=True,
                            price_basis="adjusted_research")
    return path, manifest


def test_complete_early_close_and_zero_volume(tmp_path: Path) -> None:
    schedule = Schedule([date(2026, 7, 2)], minutes=210)
    path, manifest = dataset(tmp_path, schedule)
    bars, stats = runner.aggregate_minutes(path, manifest, schedule)
    assert len(bars) == 7
    assert bars[0].time == schedule.sessions[date(2026, 7, 2)][0]
    assert bars[-1].time_close == schedule.sessions[date(2026, 7, 2)][1]
    assert bars[0].open == 100 and bars[0].high == 102
    assert bars[0].low == 99 and bars[0].close == 101 and bars[0].volume == 0
    assert stats["complete_sessions"] == 1
    runner._write_dataset(bars, manifest, tmp_path)
    derived = read_manifest(tmp_path / "manifest.toml")
    assert derived.interval_minutes == 30
    assert derived.price_basis == "adjusted_research"


def test_halt_crossing_candle_uses_only_observed_minutes(tmp_path: Path) -> None:
    schedule = Schedule([date(2020, 3, 16)])
    path, manifest = dataset(tmp_path, schedule)
    bars, stats = runner.aggregate_minutes(path, manifest, schedule)
    assert len(bars) == 13
    assert stats["halted_minute_slots"] == 14
    assert stats["source_rows"] == 376
    assert bars[0].time.minute == 30 and bars[0].time_close.minute == 0


def test_missing_minute_in_internal_session_is_error(tmp_path: Path) -> None:
    days = [date(2026, 6, day) for day in (1, 2, 3)]
    schedule = Schedule(days)
    missing = schedule.sessions[days[1]][0] + timedelta(minutes=15)
    path, manifest = dataset(tmp_path, schedule, {missing})
    with pytest.raises(ValueError, match="incomplete internal minute sessions"):
        runner.aggregate_minutes(path, manifest, schedule)


def test_incomplete_boundary_session_is_dropped(tmp_path: Path) -> None:
    days = [date(2026, 6, day) for day in (1, 2, 3)]
    schedule = Schedule(days)
    path, manifest = dataset(tmp_path, schedule, {schedule.sessions[days[0]][0]})
    bars, stats = runner.aggregate_minutes(path, manifest, schedule)
    assert len(bars) == 26
    assert bars[0].time.date() == days[1]
    assert stats["dropped_boundary_sessions"] == ["2026-06-01"]


def test_whole_missing_internal_session_is_error(tmp_path: Path) -> None:
    days = [date(2026, 6, day) for day in (1, 2, 3)]
    schedule = Schedule(days)
    opening, closing = schedule.sessions[days[1]]
    omit = {opening + timedelta(minutes=i) for i in range(int((closing - opening)
                                                             / timedelta(minutes=1)))}
    path, manifest = dataset(tmp_path, schedule, omit)
    with pytest.raises(ValueError, match="missing internal exchange session"):
        runner.aggregate_minutes(path, manifest, schedule)


def test_duplicate_minutes_are_rejected(tmp_path: Path) -> None:
    schedule = Schedule([date(2026, 6, 1)])
    path, manifest = dataset(tmp_path, schedule)
    rows = path.read_text().splitlines()
    path.write_text("\n".join([rows[0], rows[1], *rows[1:]]) + "\n")
    with pytest.raises(ValueError, match="aligned, unique, and sorted"):
        runner.aggregate_minutes(path, manifest, schedule)


def test_session_split_never_crosses_a_day(tmp_path: Path) -> None:
    days = [date(2026, 6, day) for day in range(1, 11)]
    schedule = Schedule(days)
    path, manifest = dataset(tmp_path, schedule)
    bars, _ = runner.aggregate_minutes(path, manifest, schedule)
    evaluation, heldout = runner.split_sessions(bars)
    assert heldout == schedule.sessions[days[7]][0]
    assert evaluation < heldout


def test_buy_and_hold_is_cash_funded_and_keeps_position_open(tmp_path: Path) -> None:
    schedule = Schedule([date(2026, 6, 1)])
    path, manifest = dataset(tmp_path, schedule)
    bars, _ = runner.aggregate_minutes(path, manifest, schedule)
    execution = ExecutionConfig(slippage_bps=Decimal("5"), fee_per_order=Decimal("1"))
    result = runner.buy_and_hold(bars, execution)
    assert len(result.fills) == 1 and result.fills[0].quantity == 99
    assert result.equity[0].cash >= 0
    assert result.equity[-1].equity == result.equity[-1].cash + Decimal("9999")
    summary = summarize_result(result)
    assert summary["closed_trade_count"] == 0
    assert summary["win_rate"] is None and summary["profit_factor"] is None
    assert summary["fees"] == 1


def test_comparison_smoke_uses_real_calendar_and_fresh_heldout_cash(tmp_path: Path) -> None:
    days = [date(2026, 6, day) for day in range(1, 13)]
    calendar = ExchangeCalendar(days[0], days[-1])
    schedule = Schedule([])
    schedule.sessions = {day: bounds for day in days
                         if (bounds := calendar.bounds(day, "regular")) is not None}
    source, _ = dataset(tmp_path, schedule)
    manifest_path = tmp_path / "source.toml"
    manifest_path.write_text('''[data]
feed = "fixture"
interval_minutes = 1
coverage = "regular"
adjustment = "all"
action_free = false
start_date = 2026-06-01
end_date = 2026-06-12
symbols = ["QQQ"]
synthetic = true
price_basis = "adjusted_research"
''')
    output = tmp_path / "comparison"
    report = runner.run_comparison(source, manifest_path, output)
    assert set(report["results"]) == {"full", "in_sample", "held_out"}
    for period in report["results"].values():
        assert set(period) == {"raw", "adx", "wma", "adx_wma", "turtle_s2", "buy_and_hold"}
        assert period["raw"]["starting_equity"] == 10000
    saved = json.loads((output / "comparison.json").read_text())
    assert saved["fingerprint"] == report["fingerprint"]
    heldout_fills = json.loads((output / "held_out/buy_and_hold/fills.json").read_text())
    assert heldout_fills[0]["time"] == report["held_out_start"]
    assert (output / "report.md").exists() and (output / "provenance.json").exists()
    with pytest.raises(FileExistsError):
        runner.run_comparison(source, manifest_path, output)
