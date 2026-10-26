import csv
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from turtle_trader import backtest
from turtle_trader.backtest import BacktestResult, run_backtest
from turtle_trader.domain import (
    Bar,
    ExecutionConfig,
    RiskConfig,
    StrategyConfig,
    StrategyEvents,
)
from turtle_trader.persistence import RunStore
from turtle_trader.reporting import export_report_csv, export_report_json, summarize_result
from turtle_trader.research import (
    ADJUSTED_RESEARCH_WARNING,
    research_warnings,
    validate_price_basis,
)


class AlwaysOpen:
    def eligible(self, at, market_hours="regular"):
        return True


def test_price_basis_validation_and_disclosure() -> None:
    assert validate_price_basis("action_free") == "action_free"
    assert validate_price_basis("adjusted_research") == "adjusted_research"
    assert research_warnings("action_free") == []
    assert research_warnings("adjusted_research") == [ADJUSTED_RESEARCH_WARNING]
    for value in ("adjusted", "total_return", "", "unknown"):
        with pytest.raises(ValueError, match="unknown price basis"):
            validate_price_basis(value)


def test_backtest_basis_is_a_label_and_legacy_default_is_action_free(monkeypatch) -> None:
    start = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
    bars = [
        Bar("ABC", start, start + timedelta(minutes=5), 100, 101, 99, 100, 1000),
        Bar("ABC", start + timedelta(minutes=5), start + timedelta(minutes=10),
            100, 101, 99, 100, 1000),
    ]
    class EntryOnce:
        def __init__(self, config):
            self.calls = 0

        def on_bar(self, current):
            entry = self.calls == 0
            self.calls += 1
            return StrategyEvents(
                current.symbol,
                current.time,
                current.time_close,
                held=True,
                entry=entry,
                initial_stop=90,
                active_stop=90,
            )

    monkeypatch.setattr(backtest, "Strategy", EntryOnce)
    configs = (StrategyConfig(), RiskConfig(), ExecutionConfig(fee_per_order=Decimal("1")))
    action_free = run_backtest(bars, *configs, calendar=AlwaysOpen())
    adjusted = run_backtest(bars, *configs, calendar=AlwaysOpen(), price_basis="adjusted_research")

    assert action_free.price_basis == "action_free"
    assert action_free.research_warnings == []
    assert adjusted.price_basis == "adjusted_research"
    assert adjusted.research_warnings == [ADJUSTED_RESEARCH_WARNING]
    assert adjusted.metrics == action_free.metrics
    assert adjusted.events == action_free.events
    assert adjusted.fills == action_free.fills
    assert adjusted.fills
    assert not hasattr(adjusted, "dividends")
    assert not hasattr(adjusted, "share_transformations")

    with pytest.raises(ValueError, match="unknown price basis"):
        run_backtest(bars, *configs, calendar=AlwaysOpen(), price_basis="close_adjusted")  # type: ignore[arg-type]


def test_adjusted_basis_survives_sqlite_load_and_report_regeneration(tmp_path) -> None:
    store = RunStore(tmp_path / "runs.sqlite")
    saved_result = BacktestResult(
        price_basis="adjusted_research",
        research_warnings=[ADJUSTED_RESEARCH_WARNING],
    )
    run_id = store.save("backtest", {}, "data-hash", saved_result, "source-hash")
    loaded = store.load(run_id)

    summary = summarize_result(loaded)
    assert summary["price_basis"] == "adjusted_research"
    assert summary["research_warnings"] == [ADJUSTED_RESEARCH_WARNING]

    report_path = export_report_json(loaded, tmp_path / "report.json")
    report = json.loads(report_path.read_text())
    assert report["price_basis"] == "adjusted_research"
    assert report["research_warnings"] == [ADJUSTED_RESEARCH_WARNING]
    assert report["summary"]["price_basis"] == "adjusted_research"


def test_saved_import_basis_can_come_from_run_config(tmp_path) -> None:
    store = RunStore(tmp_path / "runs.sqlite")
    run_id = store.save(
        "import",
        {"price_basis": "adjusted_research", "research_warnings": [ADJUSTED_RESEARCH_WARNING]},
        "data-hash",
        {"equity": [], "fills": []},
        "source-hash",
    )
    loaded = store.load(run_id)
    assert summarize_result(loaded)["price_basis"] == "adjusted_research"
    output = export_report_csv(loaded, tmp_path / "config-only.csv")
    with output.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["price_basis"] == "adjusted_research"
    assert ADJUSTED_RESEARCH_WARNING in rows[0]["research_warnings"]


def test_report_nested_metadata_and_conflicting_or_empty_bases() -> None:
    assert summarize_result({"metadata": {"price_basis": "adjusted_research"}})[
        "price_basis"] == "adjusted_research"
    assert summarize_result({"manifest": {"price_basis": "adjusted_research"}})[
        "research_warnings"] == [ADJUSTED_RESEARCH_WARNING]
    with pytest.raises(ValueError, match="conflicting price basis"):
        summarize_result({"price_basis": "action_free", "manifest": {
            "price_basis": "adjusted_research"}})
    with pytest.raises(ValueError, match="unknown price basis"):
        summarize_result({"price_basis": ""})


def test_legacy_report_defaults_to_action_free() -> None:
    summary = summarize_result({"equity": [], "fills": []})
    assert summary["price_basis"] == "action_free"
    assert summary["research_warnings"] == []
