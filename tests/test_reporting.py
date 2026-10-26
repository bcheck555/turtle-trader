import csv
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from turtle_trader.logging import JsonFormatter
from turtle_trader.reporting import (
    export_report_csv,
    export_report_json,
    jsonable,
    summarize_result,
)


def sample_result() -> dict[str, object]:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    t1 = datetime(2026, 1, 2, tzinfo=UTC)
    return {
        "equity": [
            {"time": t0, "equity": Decimal("100"), "exposure": Decimal("20")},
            {"time": t1, "equity": Decimal("110"), "exposure": Decimal("30")},
        ],
        "fills": [
            {"symbol": "ABC", "side": "buy", "quantity": 2, "price": Decimal("10"), "time": t0},
            {"symbol": "ABC", "side": "sell", "quantity": 2, "price": Decimal("15"), "time": t1},
        ],
    }


def test_summary_metrics_and_exact_time_benchmark() -> None:
    result = sample_result()
    benchmark = [(result["equity"][0]["time"], Decimal("50")),
                 (result["equity"][1]["time"], Decimal("55"))]  # type: ignore[index]
    summary = summarize_result(result, benchmark)
    assert summary["total_return"] == pytest.approx(0.1)
    assert summary["max_drawdown"] == 0
    assert summary["win_rate"] == 1
    assert summary["profit_factor"] is None  # positive-only ratio is unbounded
    assert summary["gross_turnover"] == 50
    assert summary["turnover"] == pytest.approx(0.5)
    assert summary["average_exposure"] == 25
    assert summary["benchmark_excess_return"] == 0


def test_jsonable_converts_decimal_and_datetime() -> None:
    result = jsonable({"amount": Decimal("1.20"), "time": datetime(2026, 1, 1, tzinfo=UTC)})
    assert result == {"amount": "1.20", "time": "2026-01-01T00:00:00+00:00"}


def test_empty_result_has_undefined_metrics_as_null() -> None:
    summary = summarize_result({"equity": [], "fills": []})
    assert summary["total_return"] is None
    assert summary["win_rate"] is None
    assert summary["profit_factor"] is None
    assert summary["max_drawdown"] is None


def test_trade_metrics_are_fee_inclusive_and_count_flat_to_flat_cycles() -> None:
    result = {
        "fills": [
            {"symbol": "ABC", "side": "buy", "quantity": 4, "price": 10, "fee": 4},
            {"symbol": "ABC", "side": "sell", "quantity": 2, "price": 14, "fee": 2},
            {"symbol": "ABC", "side": "sell", "quantity": 2, "price": 9, "fee": 2},
        ],
        "equity": [],
    }
    summary = summarize_result(result)
    assert summary["realized_pnl"] == pytest.approx(-2)
    assert summary["closed_trade_count"] == 1
    assert summary["win_rate"] == 0
    assert summary["profit_factor"] == 0


def test_partial_sale_is_not_a_closed_trade_until_position_is_flat() -> None:
    summary = summarize_result({
        "equity": [],
        "fills": [
            {"symbol": "ABC", "side": "buy", "quantity": 4, "price": 10, "fee": 4},
            {"symbol": "ABC", "side": "sell", "quantity": 2, "price": 12, "fee": 2},
        ],
    })
    assert summary["realized_pnl"] == pytest.approx(0)
    assert summary["closed_trade_count"] == 0
    assert summary["win_rate"] is None


def test_net_loss_caused_by_fees_is_reported_as_loss() -> None:
    summary = summarize_result({
        "equity": [],
        "fills": [
            {"symbol": "ABC", "side": "buy", "quantity": 1, "price": 10, "fee": 0.5},
            {"symbol": "ABC", "side": "sell", "quantity": 1, "price": 10.25, "fee": 0.5},
        ],
    })
    assert summary["realized_pnl"] == pytest.approx(-0.75)
    assert summary["win_rate"] == 0
    assert summary["profit_factor"] == 0


def test_breakeven_cycle_counts_in_win_rate_denominator() -> None:
    summary = summarize_result({
        "equity": [],
        "fills": [
            {"symbol": "ABC", "side": "buy", "quantity": 1, "price": 10, "fee": 0.5},
            {"symbol": "ABC", "side": "sell", "quantity": 1, "price": 11, "fee": 0.5},
        ],
    })
    assert summary["realized_pnl"] == pytest.approx(0)
    assert summary["closed_trade_count"] == 1
    assert summary["win_rate"] == 0
    assert summary["profit_factor"] is None


def test_starting_cash_and_exposure_units_are_explicit() -> None:
    result = {
        "metrics": {"starting_cash": Decimal("1000")},
        "equity": [
            {"time": 1, "equity": 900, "exposure": 450},
            {"time": 2, "equity": 1100, "exposure": 550},
        ],
        "fills": [{"quantity": 1, "price": 500}],
    }
    summary = summarize_result(result)
    assert summary["total_return"] == pytest.approx(0.1)
    assert summary["average_exposure"] == 500
    assert summary["average_exposure_dollars"] == 500
    assert summary["max_exposure_fraction"] == pytest.approx(0.5)
    assert summary["gross_turnover"] == 500
    assert summary["gross_turnover_dollars"] == 500
    assert summary["turnover"] == 0.5
    assert summary["turnover_multiple"] == 0.5


def test_benchmark_mismatch_is_reported() -> None:
    result = sample_result()
    summary = summarize_result(result, [(datetime(2026, 1, 1, tzinfo=UTC), 50)])
    assert summary["benchmark_return"] is None
    assert summary["benchmark_status"] == "endpoint_mismatch"


def test_exports_json_and_csv(tmp_path: Path) -> None:
    result = sample_result()
    json_path = export_report_json(result, tmp_path / "nested" / "report.json")
    payload = json.loads(json_path.read_text())
    assert payload["summary"]["total_return"] == pytest.approx(0.1)
    assert payload["result"]["fills"][0]["price"] == "10"

    csv_path = export_report_csv(result, tmp_path / "fills.csv")
    with csv_path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["record_type"] for row in rows] == ["equity", "equity", "fill", "fill"]


def test_json_formatter_redacts_credentials() -> None:
    import logging

    record = logging.LogRecord("test", logging.INFO, "", 0, "api_key=abc", (), None)
    record.password = "secret-value"
    record.order_id = "order-1"
    output = JsonFormatter().format(record)
    assert "abc" not in output
    assert "secret-value" not in output
    assert "order-1" in output
