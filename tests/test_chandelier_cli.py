import csv
import json
from pathlib import Path

import pytest

from turtle_trader.cli import main
from turtle_trader.persistence import RunStore
from turtle_trader.research import CHANDELIER_STRATEGY_WARNING


@pytest.mark.parametrize("timeframe,ratchet", [("session", True), ("intraday", False)])
def test_chandelier_cli_identity_and_diagnostics_roundtrip(
    tmp_path: Path, timeframe: str, ratchet: bool,
) -> None:
    demo = tmp_path / "demo"
    assert main(["demo", str(demo)]) == 0
    config = tmp_path / "chandelier.toml"
    config.write_text(
        '[strategy]\nday_trading=false\nexit_strategy="chandelier"\n'
        f'chandelier_timeframe="{timeframe}"\nchandelier_ratchet={str(ratchet).lower()}\n',
        encoding="utf-8",
    )
    database = tmp_path / "runs.sqlite"
    output = tmp_path / "backtest.json"
    base = [str(demo / "bars.csv"), "--manifest", str(demo / "data.toml"),
            "--config", str(config), "--database", str(database)]
    assert main(["replay", *base]) == 0
    assert main(["backtest", *base, "--output", str(output)]) == 0
    store = RunStore(database)
    runs = store.list_runs()
    assert len(runs) == 2
    for run in runs:
        loaded = store.load(run["run_id"])
        result = loaded["result"]
        assert result["exit_strategy"] == "chandelier"
        assert result["strategy_settings"]["chandelier_timeframe"] == timeframe
        assert result["strategy_settings"]["chandelier_ratchet"] is ratchet
        assert result["strategy_warnings"] == [CHANDELIER_STRATEGY_WARNING]
        assert result["metadata"]["exit_strategy"] == "chandelier"
        assert any(e["chandelier_atr"] is not None for e in result["events"])
        assert all(e["exit_strategy"] == "chandelier" for e in result["events"])
        assert result["events"][-1]["atr"] is not None
    payload = json.loads(output.read_text())
    assert payload["summary"]["exit_strategy"] == "chandelier"
    assert payload["strategy_settings"]["chandelier_timeframe"] == timeframe
    assert payload["summary"]["strategy_warnings"] == [CHANDELIER_STRATEGY_WARNING]
    assert main(["report", runs[-1]["run_id"], "--database", str(database),
                 "--output", str(output)]) == 0
    with output.with_suffix(".csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows and all(row["exit_strategy"] == "chandelier" for row in rows)
    assert json.loads(rows[0]["strategy_settings"])["chandelier_timeframe"] == timeframe
    assert json.loads(rows[0]["strategy_warnings"]) == [CHANDELIER_STRATEGY_WARNING]
    assert main(["walk-forward", *base, "--warmup-days", "30", "--fold-days", "7",
                 "--output", str(tmp_path / "fold.json")]) == 0
    for path in tmp_path.glob("fold-*.json"):
        fold = json.loads(path.read_text())
        assert fold["exit_strategy"] == "chandelier"
        assert fold["strategy_settings"]["chandelier_ratchet"] is ratchet


def test_regenerated_chandelier_config_only_preserves_adjusted_basis(tmp_path: Path) -> None:
    database = tmp_path / "saved.sqlite"
    run_id = RunStore(database).save(
        "backtest", {"strategy": {"exit_strategy": "chandelier",
                                  "chandelier_timeframe": "intraday",
                                  "chandelier_ratchet": False},
                     "price_basis": "adjusted_research"}, "data",
        {"equity": [], "fills": []}, "source")
    output = tmp_path / "report.json"
    assert main(["report", run_id, "--database", str(database),
                 "--output", str(output)]) == 0
    payload = json.loads(output.read_text())
    assert payload["exit_strategy"] == "chandelier"
    assert payload["price_basis"] == "adjusted_research"
    assert payload["strategy_warnings"] == [CHANDELIER_STRATEGY_WARNING]
    assert payload["research_warnings"]
