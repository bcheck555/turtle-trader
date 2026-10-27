"""The research runner changes storage, not production report values."""

import csv
import gzip
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from research.run_turtle_minute import stream_result
from turtle_trader.backtest import BacktestResult, EngineEvent, EquitySnapshot
from turtle_trader.domain import Fill
from turtle_trader.reporting import export_report_csv


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
