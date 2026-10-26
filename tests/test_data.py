from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from turtle_trader.config import load_config
from turtle_trader.data import ExchangeCalendar, load_dataset, read_manifest


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


def test_price_basis_defaults_legacy_and_explicit_adjusted_research(tmp_path: Path) -> None:
    csv, path = write_data(tmp_path, "ABC,2026-01-05T14:30:00Z,100,102,99,101,1000\n")
    assert read_manifest(path).price_basis == "action_free"
    original = path.read_text()
    path.write_text(original.replace('adjustment="split"', 'adjustment="all"')
                    .replace("action_free=true", "action_free=false")
                    + 'price_basis="adjusted_research"\n')
    dataset = load_dataset(csv, path)
    assert dataset.manifest.price_basis == "adjusted_research"
    assert dataset.manifest.action_free is False


@pytest.mark.parametrize(("old", "new"), [
    ('feed="synthetic"', 'feed=123'),
    ('feed="synthetic"', 'feed=" "'),
    ("interval_minutes=5", "interval_minutes=true"),
    ("interval_minutes=5", "interval_minutes=5.0"),
    ('coverage="custom"', 'coverage=true'),
    ('adjustment="split"', 'adjustment=["split"]'),
    ("action_free=true", "action_free=1"),
    ("action_free=true", 'action_free="true"'),
    ("action_free=true", "action_free=false"),
    ("synthetic=true", 'synthetic="true"'),
    ('symbols=["ABC"]', 'symbols=["ABC","ABC"]'),
    ('symbols=["ABC"]', 'symbols=[" "]'),
    ('symbols=["ABC"]', 'symbols=[123]'),
    ('start_date="2026-01-05"', "start_date=20260105"),
    ('start_date="2026-01-05"', 'start_date="2026-02-30"'),
    ('end_date="2026-01-05"', 'end_date="2026-01-04"'),
    ('feed="synthetic"\n', ""),
])
def test_malformed_manifest_types_and_missing_keys_fail_safe(
    tmp_path: Path, old: str, new: str,
) -> None:
    _, path = write_data(tmp_path, "")
    path.write_text(path.read_text().replace(old, new))
    with pytest.raises(ValueError):
        read_manifest(path)


@pytest.mark.parametrize("extra", [
    'price_basis="unknown"\n',
    'price_basis=123\n',
    'price_basis="adjusted_research"\n',  # Missing all-adjustment and non-attestation.
    'unknown="value"\n',
    'timestamp_convention="bar_close"\n',
])
def test_invalid_manifest_modes(tmp_path: Path, extra: str) -> None:
    _, path = write_data(tmp_path, "")
    path.write_text(path.read_text() + extra)
    with pytest.raises(ValueError):
        read_manifest(path)


def test_adjusted_research_cannot_claim_action_free(tmp_path: Path) -> None:
    _, path = write_data(tmp_path, "")
    path.write_text(path.read_text().replace('adjustment="split"', 'adjustment="all"')
                    + 'price_basis="adjusted_research"\n')
    with pytest.raises(ValueError, match="action_free=false"):
        read_manifest(path)


@pytest.mark.parametrize("strict", [True, False])
def test_outside_exchange_coverage_always_rejected(tmp_path: Path, strict: bool) -> None:
    paths = write_data(tmp_path, "ABC,2026-01-05T14:00:00Z,100,102,99,101,1000\n", "regular")
    with pytest.raises(ValueError, match="outside declared exchange coverage"):
        load_dataset(*paths, strict=strict)


@pytest.mark.parametrize("row", [
    "ABC,2026-01-05T14:30:00Z,100,102,99,101,1000,extra\n",
    "ABC\n",
    "ABC,2026-01-05T14:30:00Z,100,102,99,101\n",
])
def test_ragged_csv_rejected_with_row_diagnostic(tmp_path: Path, row: str) -> None:
    with pytest.raises(ValueError, match="CSV row 2"):
        load_dataset(*write_data(tmp_path, row))


def test_duplicate_csv_header_rejected(tmp_path: Path) -> None:
    csv, path = write_data(tmp_path, "")
    csv.write_text("symbol,symbol,timestamp,open,high,low,close,volume\n")
    with pytest.raises(ValueError, match="CSV columns"):
        load_dataset(csv, path)


def test_strict_complete_import_matches_shared_coverage(tmp_path: Path) -> None:
    opening = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    rows = "".join(
        f"ABC,{(opening + timedelta(minutes=5 * n)).isoformat()},100,102,99,101,1000\n"
        for n in range(78)
    )
    dataset = load_dataset(*write_data(tmp_path, rows, "regular"))
    assert len(dataset.bars) == 78 and dataset.warnings == []


@pytest.mark.parametrize("text", ["", "[other]\nfeed='x'\n", "data=true\n"])
def test_manifest_requires_exact_data_table(tmp_path: Path, text: str) -> None:
    path = tmp_path / "bad.toml"
    path.write_text(text)
    with pytest.raises(ValueError, match="one \\[data\\] table"):
        read_manifest(path)
