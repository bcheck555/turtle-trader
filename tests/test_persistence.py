import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from turtle_trader.persistence import RunStore


@dataclass
class Example:
    at: datetime
    amount: Decimal
    path: Path


def test_run_store_round_trip_and_listing(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.sqlite")
    at = datetime(2026, 1, 2, tzinfo=UTC)
    run_id = store.save("backtest", Example(at, Decimal("12.30"), Path("bars.csv")), "data", {
        "fills": [{"price": Decimal("1.25"), "time": at}],
    }, "source")

    loaded = store.load(run_id)
    assert loaded["kind"] == "backtest"
    assert loaded["config"] == {"at": at, "amount": Decimal("12.30"), "path": Path("bars.csv")}
    assert loaded["result"]["fills"][0] == {"price": Decimal("1.25"), "time": at}
    assert loaded["data_hash"] == "data"
    assert store.list_runs()[0]["run_id"] == run_id


@pytest.mark.parametrize("value", [float("nan"), float("inf"), Decimal("NaN"), Decimal("Infinity")])
def test_rejects_non_finite_numbers_without_partial_run(tmp_path: Path, value: object) -> None:
    store = RunStore(tmp_path / "runs.sqlite")
    with pytest.raises(ValueError):
        store.save("backtest", {}, "data", {"bad": value}, "source")
    assert store.list_runs() == []


def test_load_missing_run_raises_key_error(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        RunStore(tmp_path / "runs.sqlite").load("missing")


def test_legacy_database_rejected_without_modification(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY)")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="incompatible"):
        RunStore(path)
    assert path.read_bytes() == before
