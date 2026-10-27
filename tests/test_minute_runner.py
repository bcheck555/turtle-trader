"""The research runner changes storage, not production report values."""

import csv
import gzip
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from research.run_turtle_minute import ROOT, research_config, stream_result
from turtle_trader.backtest import BacktestResult, EngineEvent, EquitySnapshot
from turtle_trader.config import load_config, resolved_config
from turtle_trader.domain import Fill
from turtle_trader.reporting import export_report_csv


def test_runner_defaults_preserve_previous_long_only_settings():
    old = load_config(ROOT / "configs/system2-available-cash.toml", (
        "strategy.interval_minutes=1", 'strategy.exit_strategy="turtle_channel"',
        'strategy.chandelier_timeframe="session"',
    ))
    assert resolved_config(research_config("turtle_channel")) == resolved_config(old)


def test_atr_control_and_short_case_differ_only_in_short_permission():
    long = resolved_config(research_config("chandelier", "atr", False, Decimal(0)))
    short = resolved_config(research_config("chandelier", "atr", True, Decimal(0)))
    assert short["execution"]["shortable_symbols"] == ["QQQ"]
    assert short["execution"]["borrow_rate"] == "0"
    assert short["risk"]["position_sizing"] == "atr"
    assert short["risk"]["max_units"] == 1
    assert short["strategy"]["allow_short"] is True
    short["strategy"]["allow_short"] = False
    assert short == long


def test_unsupported_short_sizing_and_unspecified_borrow_remain_rejected():
    with pytest.raises(ValueError, match="ATR sizing"):
        research_config("turtle_channel", "available_cash", True, Decimal(0))
    with pytest.raises(ValueError, match="explicit annual"):
        research_config("turtle_channel", "atr", True)


def test_streamed_exports_preserve_production_values_and_full_event_details(tmp_path):
    at = datetime(2020, 3, 9, 13, 49, 13, tzinfo=UTC)
    result = BacktestResult()
    result.equity = [EquitySnapshot(at + timedelta(seconds=47), Decimal("99.125"),
                                    Decimal("1099.125"), Decimal("1000"))]
    result.fills = [Fill("order-1", "QQQ", "buy", 10, Decimal("100.0005"),
                         Decimal("0.25"), at, 'quoted, "reason"')]
    result.events = [EngineEvent("QQQ", at, "test", detail='quoted, "detail"\nsecond line')]
    output = tmp_path / "streamed"
    output.mkdir()
    stream_result(result, output)
    export_report_csv(result, tmp_path / "standard.csv")
    with (output / "report.csv").open(newline="") as handle:
        streamed = list(csv.DictReader(handle))
    with (tmp_path / "standard.csv").open(newline="") as handle:
        standard = list(csv.DictReader(handle))
    assert len(streamed) == len(standard) == 2
    for actual, reference in zip(streamed, standard, strict=True):
        assert all(reference[key] == value for key, value in actual.items())
    with gzip.open(output / "events.jsonl.gz", "rt") as handle:
        events = [json.loads(line) for line in handle]
    assert events[0]["detail"] == result.events[0].detail
    assert events[0]["time"] == at.isoformat()
    fills = json.loads((output / "fills.json").read_text())
    assert fills[0]["price"] == "100.0005"
    assert fills[0]["quantity"] == 10
    assert json.loads((output / "audit.json").read_text())["exit_strategy"] == "turtle_channel"
