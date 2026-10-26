import csv
import json
from pathlib import Path

import pytest

from turtle_trader.backtest import BacktestResult
from turtle_trader.persistence import RunStore
from turtle_trader.reporting import export_report_csv, export_report_json, summarize_result
from turtle_trader.research import CHANDELIER_STRATEGY_WARNING


def test_backtest_result_reports_chandelier_identity_and_settings() -> None:
    settings = {
        "exit_strategy": "chandelier",
        "chandelier_timeframe": "session",
        "chandelier_length": 22,
        "chandelier_atr_length": 14,
        "chandelier_multiple": 3.0,
        "chandelier_ratchet": True,
    }
    result = BacktestResult(
        exit_strategy="chandelier",
        strategy_settings=settings,
        strategy_warnings=[CHANDELIER_STRATEGY_WARNING],
    )

    summary = summarize_result(result)

    assert summary["exit_strategy"] == "chandelier"
    assert summary["strategy_settings"] == settings
    assert summary["strategy_warnings"] == [CHANDELIER_STRATEGY_WARNING]
    assert summary["price_basis"] == "action_free"
    assert summary["research_warnings"] == []


def test_saved_config_identity_regenerates_json_and_empty_csv(tmp_path) -> None:
    store = RunStore(tmp_path / "runs.sqlite")
    config = {
        "strategy": {
            "exit_strategy": "chandelier",
            "chandelier_timeframe": "intraday",
            "chandelier_length": 18,
            "chandelier_atr_length": 20,
            "chandelier_multiple": 2.5,
            "chandelier_ratchet": False,
        }
    }
    run_id = store.save("backtest", config, "data-hash", {"equity": [], "fills": []}, "src-hash")
    loaded = store.load(run_id)

    json_path = export_report_json(loaded, tmp_path / "report.json")
    report = json.loads(json_path.read_text())
    assert report["exit_strategy"] == "chandelier"
    assert report["strategy_settings"] == config["strategy"]
    assert report["strategy_warnings"] == [CHANDELIER_STRATEGY_WARNING]
    assert report["summary"]["exit_strategy"] == "chandelier"

    csv_path = export_report_csv(loaded, tmp_path / "report.csv")
    with csv_path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["record_type"] == "metadata"
    assert rows[0]["exit_strategy"] == "chandelier"
    assert json.loads(rows[0]["strategy_settings"]) == config["strategy"]
    assert json.loads(rows[0]["strategy_warnings"]) == [CHANDELIER_STRATEGY_WARNING]
    assert rows[0]["price_basis"] == "action_free"
    assert rows[0]["research_warnings"] == "[]"


def test_csv_rows_keep_strategy_and_research_labels_separate(tmp_path: Path) -> None:
    result = {
        "exit_strategy": "chandelier",
        "strategy_settings": {"exit_strategy": "chandelier", "chandelier_length": 22},
        "price_basis": "adjusted_research",
        "equity": [{"time": "2025-01-02T15:00:00+00:00", "equity": 100, "exposure": 20}],
        "fills": [],
    }
    csv_path = export_report_csv(result, tmp_path / "chandelier-report.csv")
    with csv_path.open(newline="") as handle:
        record = next(csv.DictReader(handle))
    assert record["record_type"] == "equity"
    assert record["exit_strategy"] == "chandelier"
    assert json.loads(record["strategy_settings"])["chandelier_length"] == 22
    assert json.loads(record["strategy_warnings"]) == [CHANDELIER_STRATEGY_WARNING]
    assert record["price_basis"] == "adjusted_research"
    assert CHANDELIER_STRATEGY_WARNING not in record["research_warnings"]


def test_empty_result_uses_turtle_channel_defaults() -> None:
    assert summarize_result({"equity": [], "fills": []})["exit_strategy"] == "turtle_channel"
    assert summarize_result({"equity": [], "fills": [], "price_basis": "adjusted_research"})[
        "strategy_warnings"
    ] == []


@pytest.mark.parametrize(
    "result",
    [
        {"exit_strategy": "", "equity": [], "fills": []},
        {
            "exit_strategy": "turtle_channel",
            "config": {"strategy": {"exit_strategy": "chandelier"}},
            "equity": [],
            "fills": [],
        },
        {"exit_strategy": "chandelier", "strategy_settings": "not-a-mapping"},
        {
            "exit_strategy": "chandelier",
            "strategy_settings": {"chandelier_length": "twenty-two"},
        },
    ],
)
def test_invalid_or_conflicting_strategy_metadata_is_rejected(result) -> None:
    with pytest.raises(ValueError):
        summarize_result(result)
