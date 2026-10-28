"""Fixed AAPL video-window and sensitivity experiment contracts."""

import csv
import importlib.util
import json
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from turtle_trader.data import ExchangeCalendar
from turtle_trader.domain import Bar

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "aapl_squeeze_runner", ROOT / "research/run_aapl_squeeze.py")
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
with pytest.MonkeyPatch.context() as imports:
    imports.syspath_prepend(str(ROOT))
    SPEC.loader.exec_module(runner)


def candle(day: int, index: int = 0) -> Bar:
    at = datetime(2026, 6, day, 13, 30, tzinfo=UTC) + timedelta(minutes=30 * index)
    return Bar("AAPL", at, at + timedelta(minutes=30), 100, 101, 99, 100, 1000)


def test_inclusive_end_date_keeps_final_candle_and_excludes_following_day() -> None:
    bars = [candle(1), candle(2), candle(2, 12), candle(3)]
    window = runner.window_for_dates(bars, date(2026, 6, 2), date(2026, 6, 2))
    assert window.start == bars[1].time
    assert window.end == bars[2].time_close
    assert window.end < bars[3].time


@pytest.mark.parametrize("start,end", [
    (date(2026, 5, 31), date(2026, 6, 2)),
    (date(2026, 6, 2), date(2026, 6, 4)),
    (date(2026, 6, 3), date(2026, 6, 2)),
])
def test_incomplete_window_coverage_is_not_silently_shortened(start: date, end: date) -> None:
    with pytest.raises(ValueError):
        runner.window_for_dates([candle(1), candle(2), candle(3)], start, end)


def test_full_window_starts_at_session_boundary_after_120_observations() -> None:
    bars = [candle(day, index) for day in range(1, 13) for index in range(13)]
    window = runner.full_history_window(bars)
    assert window.start == candle(11).time
    assert window.end == bars[-1].time_close
    with pytest.raises(ValueError, match="warmup"):
        runner.full_history_window(bars[:120])


def test_short_and_cost_sensitivities_are_explicit_and_otherwise_identical() -> None:
    configs = runner.configurations()
    assert len(configs) == 8
    long = configs["both_long"]
    short = configs["both_long_short"]
    free = configs["both_long_short_no_slippage"]
    assert not long.strategy.allow_short and short.strategy.allow_short
    assert short.execution.shortable_symbols == ("AAPL",)
    assert short.execution.borrow_rate == Decimal("0.03")
    assert short.execution.initial_short_collateral == Decimal("1.5")
    assert short.execution.slippage_bps == 5 and free.execution.slippage_bps == 0
    assert short.strategy == free.strategy and short.risk == free.risk
    assert free.execution.borrow_rate == short.execution.borrow_rate
    assert short.risk.starting_cash == 10000
    assert short.risk.unit_fraction * Decimal(str(short.strategy.atr_multiple)) == Decimal("0.03")
    assert not configs["raw_long"].strategy.squeeze_use_adx
    assert not configs["raw_long"].strategy.squeeze_use_wma
    assert configs["raw_long"].strategy.squeeze_bb_multiple == 2
    assert configs["both_long_short_legacy_bb"].strategy.squeeze_bb_multiple == 1.5
    assert configs["both_long_short_legacy_bb"].strategy.squeeze_kc_multiple == 1.5
    assert configs["both_long_short_legacy_bb_no_slippage"].execution.slippage_bps == 0


def source_fixture(tmp_path: Path) -> tuple[Path, Path]:
    start, end = date(2026, 6, 1), date(2026, 6, 30)
    calendar = ExchangeCalendar(start, end)
    csv_path = tmp_path / "aapl.csv"
    with csv_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("symbol", "timestamp", "open", "high", "low", "close", "volume"))
        day = start
        while day <= end:
            bounds = calendar.bounds(day, "regular")
            if bounds:
                at, closing = bounds
                while at < closing:
                    writer.writerow(("AAPL", at.isoformat(), 100, 101, 99, 100, 1000))
                    at += timedelta(minutes=1)
            day += timedelta(days=1)
    manifest = tmp_path / "source.toml"
    manifest.write_text('''[data]
feed="fixture"
interval_minutes=1
coverage="regular"
adjustment="all"
action_free=false
start_date=2026-06-01
end_date=2026-06-30
symbols=["AAPL"]
synthetic=true
price_basis="adjusted_research"
''')
    return csv_path, manifest


def test_end_to_end_fixed_variants_fresh_cash_and_artifact_identity(tmp_path: Path) -> None:
    source, manifest = source_fixture(tmp_path)
    output = tmp_path / "results"
    report = runner.run_comparison(source, manifest, output, windows={
        "fixture": (date(2026, 6, 16), date(2026, 6, 26)),
    }, include_heldout=False)
    assert set(report["results"]) == {"full", "fixture"}
    for window in report["results"].values():
        assert len(window) == 9
        assert all(summary["starting_equity"] == 10000 for summary in window.values())
    assert report["windows"]["fixture"]["end"] == "2026-06-26T20:00:00+00:00"
    fills = json.loads((output / "fixture/buy_and_hold/fills.json").read_text())
    assert fills[0]["time"] == report["windows"]["fixture"]["start"]
    provenance = json.loads((output / "provenance.json").read_text())
    assert provenance["fingerprint"] == report["fingerprint"]
    assert provenance["source_csv_sha256"] == runner.checksum(source)
    assert provenance["derived_csv_sha256"] == runner.checksum(output / "bars.csv")
    assert (output / "report.md").exists()
    with pytest.raises(FileExistsError):
        runner.run_comparison(source, manifest, output, windows={}, include_heldout=False)


def test_other_symbols_are_rejected_before_publishing(tmp_path: Path) -> None:
    source, manifest = source_fixture(tmp_path)
    manifest.write_text(manifest.read_text().replace('symbols=["AAPL"]', 'symbols=["QQQ"]'))
    with pytest.raises(ValueError, match="AAPL-only"):
        runner.run_comparison(source, manifest, tmp_path / "wrong")
    assert not (tmp_path / "wrong").exists()
