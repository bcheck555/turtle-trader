from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from turtle_trader.config import load_config
from turtle_trader.data import ExchangeCalendar, load_dataset


def write_data(tmp_path: Path, rows: str, coverage: str = "custom") -> tuple[Path, Path]:
    csv = tmp_path / "bars.csv"
    manifest = tmp_path / "data.toml"
    csv.write_text("symbol,timestamp,open,high,low,close,volume\n" + rows)
    manifest.write_text(
        '[data]\nfeed="synthetic"\ninterval_minutes=5\n'
        f'coverage="{coverage}"\nadjustment="split"\naction_free=true\n'
        'start_date="2026-01-05"\nend_date="2026-01-05"\n'
        'symbols=["ABC"]\nsynthetic=true\n'
    )
    return csv, manifest


def test_import_and_hash(tmp_path: Path) -> None:
    paths = write_data(tmp_path, "ABC,2026-01-05T14:30:00Z,100,102,99,101,1000\n")
    data = load_dataset(*paths)
    assert len(data.bars) == 1
    assert data.bars[0].time.tzinfo == UTC
    assert len(data.data_hash) == 64
    assert data.warnings


@pytest.mark.parametrize("row", [
    "ABC,2026-01-05T14:30:00,100,102,99,101,1000\n",
    "ABC,2026-01-05T14:30:00Z,nan,102,99,101,1000\n",
    "ABC,2026-01-05T14:30:00Z,100,98,99,101,1000\n",
    "ABC,2026-01-05T14:30:00Z,100,102,99,101,-1\n",
    "ABC,2026-01-05T14:31:00Z,100,102,99,101,1000\n",
])
def test_reject_invalid_bar(tmp_path: Path, row: str) -> None:
    with pytest.raises(ValueError):
        load_dataset(*write_data(tmp_path, row))


def test_gap_rejected_but_reference_warns(tmp_path: Path) -> None:
    paths = write_data(tmp_path, "ABC,2026-01-05T14:30:00Z,100,102,99,101,1000\n", "regular")
    with pytest.raises(ValueError, match="missing candles"):
        load_dataset(*paths)
    assert "77 missing" in load_dataset(*paths, strict=False).warnings[0]


def test_duplicate_rejected(tmp_path: Path) -> None:
    row = "ABC,2026-01-05T14:30:00Z,100,102,99,101,1000\n"
    with pytest.raises(ValueError, match="duplicate"):
        load_dataset(*write_data(tmp_path, row + row))


def test_holidays_early_close_and_dst() -> None:
    calendar = ExchangeCalendar(date(2026, 1, 1), date(2026, 12, 31))
    assert not calendar.eligible(datetime(2026, 12, 25, 15, tzinfo=UTC))
    assert calendar.eligible(datetime(2026, 11, 27, 17, 55, tzinfo=UTC))
    assert not calendar.eligible(datetime(2026, 11, 27, 18, tzinfo=UTC))
    assert not calendar.eligible(datetime(2026, 11, 27, 18, tzinfo=UTC), "extended")
    assert calendar.eligible(datetime(2026, 3, 6, 14, 30, tzinfo=UTC))
    assert calendar.eligible(datetime(2026, 3, 9, 13, 30, tzinfo=UTC))
    assert not calendar.eligible(datetime(2026, 3, 8, 22, tzinfo=UTC), "extended")


def test_config_no_live_path(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text('[execution]\nmode="live"\n')
    with pytest.raises(ValueError, match="unknown execution"):
        load_config(p)
    p.write_text('[risk]\nstarting_cash="NaN"\n')
    with pytest.raises(ValueError):
        load_config(p)
    p.write_text('[strategy]\nday_trading="false"\n')
    with pytest.raises(ValueError, match="boolean"):
        load_config(p)
