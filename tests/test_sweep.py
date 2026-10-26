import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from turtle_trader import sweep
from turtle_trader.backtest import validate_backtest_inputs
from turtle_trader.config import AppConfig, load_config
from turtle_trader.data import DataManifest, Dataset, ExchangeCalendar
from turtle_trader.domain import Bar


@dataclass
class FakeResult:
    equity: list = field(default_factory=list)
    fills: list = field(default_factory=list)
    metrics: dict = field(default_factory=lambda: {"starting_cash": Decimal("100")})
    price_basis: str = "action_free"
    strategy_settings: dict = field(default_factory=dict)


def fake_backtest(bars, strategy, risk, execution, **kwargs):
    if strategy.entry_length == 99:
        raise ValueError("intentional experiment failure")
    return FakeResult(equity=[{
        "time": bars[0].time, "equity": Decimal("100") + strategy.entry_length,
        "exposure": Decimal("0"),
    }])


@pytest.fixture(autouse=True)
def fake_validation(monkeypatch) -> None:
    # These tests isolate scheduling/artifacts; engine validation has its own
    # real-session regression suite.
    monkeypatch.setattr(sweep, "validate_backtest_inputs", lambda *args, **kwargs: None)


@pytest.fixture
def data() -> Dataset:
    at = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
    return Dataset(
        [Bar("ABC", at, at + timedelta(minutes=5), 10, 11, 9, 10, 100)],
        DataManifest("test", 5, "regular", "split", True, date(2025, 1, 2),
                     date(2025, 1, 2), ("ABC",)), "test-data-hash", [],
    )


def grid(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "grid.toml"
    path.write_text("[parameters]\n" + text)
    return path


def test_grid_limits_types_duplicates_and_system_defaults(tmp_path: Path) -> None:
    path = grid(tmp_path, '"strategy.entry_length"=[20,30]\n"strategy.atr_multiple"=[2,3]\n')
    configs = sweep.expand_grid(AppConfig(), path)
    assert [(c.strategy.atr_multiple, c.strategy.entry_length) for c in configs] == [
        (2, 20), (2, 30), (3, 20), (3, 30),
    ]
    with pytest.raises(ValueError, match="max-runs"):
        sweep.expand_grid(AppConfig(), path, max_runs=3)
    path = grid(tmp_path, '"strategy.entry_length"=[20,20]\n')
    with pytest.raises(ValueError, match="duplicate"):
        sweep.expand_grid(AppConfig(), path)
    path = grid(tmp_path, '"strategy.allow_short"=[1]\n')
    with pytest.raises(ValueError, match="boolean"):
        sweep.expand_grid(AppConfig(), path)
    path = grid(tmp_path, '"strategy.entry_strategy"=["turtle_s1","turtle_s2"]\n')
    configs = sweep.expand_grid(AppConfig(), path, overrides=[])
    assert [(c.strategy.entry_length, c.strategy.exit_length) for c in configs] == [
        (20, 10), (55, 20),
    ]


def test_grid_override_precedence_matches_standalone(tmp_path: Path) -> None:
    base = tmp_path / "base.toml"
    base.write_text('schema_version=2\n[strategy]\nentry_length=30\n')
    path = grid(tmp_path, '"strategy.entry_strategy"=["turtle_s1"]\n')
    configs = sweep.expand_grid(load_config(base), path, base_path=base,
                                overrides=["risk.unit_fraction=0.02"])
    assert configs == [load_config(base, ["risk.unit_fraction=0.02",
                                          'strategy.entry_strategy="turtle_s1"'])]


def test_sweep_isolation_resume_and_failures(tmp_path: Path, monkeypatch, data: Dataset) -> None:
    monkeypatch.setattr(sweep, "run_backtest", fake_backtest)
    path = grid(tmp_path, '"strategy.entry_length"=[20,99]\n')
    configs = sweep.expand_grid(AppConfig(), path)
    destination = tmp_path / "results"
    result = sweep.run_sweep(data, configs, destination)
    assert result["exit_code"] == 1 and result["failed"] == 1
    manifest = json.loads((destination / "manifest.json").read_text())
    assert [run["status"] for run in manifest["runs"]] == ["completed", "failed"]
    completed = manifest["runs"][0]
    report = destination / completed["directory"] / "report.json"
    original = report.read_bytes()
    assert (destination / completed["directory"] / "run.sqlite").is_file()
    assert (destination / "summary.csv").is_file()
    assert sweep.run_sweep(data, configs, destination, resume=True)["failed"] == 1
    assert report.read_bytes() == original
    with pytest.raises(ValueError, match="destination exists"):
        sweep.run_sweep(data, configs, destination)
    with pytest.raises(ValueError, match="differ"):
        sweep.run_sweep(data, configs[:1], destination, resume=True)


def test_parallel_and_standalone_results_match(tmp_path: Path, monkeypatch, data: Dataset) -> None:
    monkeypatch.setattr(sweep, "run_backtest", fake_backtest)
    configs = sweep.expand_grid(AppConfig(), grid(
        tmp_path, '"strategy.entry_length"=[20,30]\n',
    ))
    sweep.run_sweep(data, configs, tmp_path / "sequential")
    sweep.run_sweep(data, configs, tmp_path / "parallel", workers=2)
    sequential = json.loads((tmp_path / "sequential/summary.json").read_text())["runs"]
    parallel = json.loads((tmp_path / "parallel/summary.json").read_text())["runs"]
    assert [r["fingerprint"] for r in sequential] == [r["fingerprint"] for r in parallel]
    assert [r["summary"] for r in sequential] == [r["summary"] for r in parallel]
    for index, config in enumerate(configs):
        standalone = sweep.execute_run(data, config, tmp_path / f"standalone-{index}")
        paired = next(r for r in sequential if r["fingerprint"] == standalone["fingerprint"])
        assert paired["summary"] == standalone["summary"]


def test_resume_reexecutes_damaged_artifacts(tmp_path: Path, monkeypatch, data: Dataset) -> None:
    monkeypatch.setattr(sweep, "run_backtest", fake_backtest)
    configs = [AppConfig()]
    destination = tmp_path / "results"
    sweep.run_sweep(data, configs, destination)
    record = json.loads((destination / "manifest.json").read_text())["runs"][0]
    report = destination / record["directory"] / "report.json"
    report.write_text("damaged")
    sweep.run_sweep(data, configs, destination, resume=True)
    assert json.loads(report.read_text())["schema_version"] == 2


def test_directory_lock_prevents_concurrent_writers(tmp_path: Path, data: Dataset) -> None:
    with sweep._exclusive(tmp_path):
        with pytest.raises(ValueError, match="in use"):
            with sweep._exclusive(tmp_path):
                pytest.fail("second writer acquired lock")


def test_malformed_resume_records_are_rejected_or_retried(
    tmp_path: Path, monkeypatch, data: Dataset,
) -> None:
    monkeypatch.setattr(sweep, "run_backtest", fake_backtest)
    destination = tmp_path / "results"
    sweep.run_sweep(data, [AppConfig()], destination)
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    record_path = destination / manifest["runs"][0]["directory"] / "run.json"
    record_path.write_text("[]")
    assert sweep.run_sweep(data, [AppConfig()], destination, resume=True)["exit_code"] == 0
    manifest_path.write_text("[]")
    with pytest.raises(ValueError, match="object"):
        sweep.run_sweep(data, [AppConfig()], destination, resume=True)


def test_data_validation_fails_before_creating_outputs(
    tmp_path: Path, monkeypatch, data: Dataset,
) -> None:
    def invalid(*args, **kwargs):
        raise ValueError("invalid sessions")
    monkeypatch.setattr(sweep, "validate_backtest_inputs", invalid)
    destination = tmp_path / "results"
    with pytest.raises(ValueError, match="invalid sessions"):
        sweep.run_sweep(data, [AppConfig()], destination)
    assert not destination.exists()


def test_benchmark_changes_identity_and_reaches_summary(
    tmp_path: Path, monkeypatch, data: Dataset,
) -> None:
    monkeypatch.setattr(sweep, "run_backtest", fake_backtest)
    benchmark = [(data.bars[0].time, Decimal("100"))]
    plain = sweep.run_metadata(AppConfig(), data, None, None)
    compared = sweep.run_metadata(AppConfig(), data, None, None, benchmark)
    assert plain["run_fingerprint"] != compared["run_fingerprint"]
    result = sweep.execute_run(data, AppConfig(), tmp_path / "run", benchmark=benchmark)
    assert result["summary"]["benchmark_status"] == "matched"


@pytest.mark.parametrize("whole_session", [False, True])
def test_real_internal_session_gap_fails_preflight(
    tmp_path: Path, monkeypatch, whole_session: bool,
) -> None:
    monkeypatch.setattr(sweep, "validate_backtest_inputs", validate_backtest_inputs)
    calendar = ExchangeCalendar(date(2025, 1, 2), date(2025, 1, 6))
    bars = []
    for day in (date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)):
        opening, closing = calendar.bounds(day, "regular")
        at = opening
        while at < closing:
            if not (day.day == 3 and (whole_session or at == opening)):
                bars.append(Bar("ABC", at, at + timedelta(minutes=5), 10, 11, 9, 10, 100))
            at += timedelta(minutes=5)
    data = Dataset(bars, DataManifest("test", 5, "regular", "split", True,
                                     date(2025, 1, 2), date(2025, 1, 6), ("ABC",)), "hash", [])
    destination = tmp_path / "results"
    with pytest.raises(ValueError, match="incomplete|missing"):
        sweep.run_sweep(data, [AppConfig()], destination)
    assert not destination.exists()
