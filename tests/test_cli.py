import hashlib
import json
from pathlib import Path

from turtle_trader.cli import main
from turtle_trader.persistence import RunStore
from turtle_trader.sweep import rule_hash


def test_source_integrity() -> None:
    spec = Path(__file__).parents[1] / "src/turtle_trader/rule_spec.md"
    assert hashlib.sha256(spec.read_bytes()).hexdigest() == rule_hash()


def test_cli_end_to_end(tmp_path: Path) -> None:
    assert main(["demo", str(tmp_path / "demo")]) == 0
    csv = str(tmp_path / "demo" / "bars.csv")
    manifest = str(tmp_path / "demo" / "data.toml")
    db = tmp_path / "runs.sqlite"
    output = tmp_path / "report.json"
    base = [csv, "--manifest", manifest, "--database", str(db)]
    assert main(["validate", *base]) == 0
    assert main(["import", *base]) == 0
    assert main(["replay", *base]) == 0
    assert main(["backtest", *base, "--output", str(output)]) == 0
    records = RunStore(db).list_runs()
    assert [record["kind"] for record in records] == ["import", "replay", "backtest"]
    record = RunStore(db).load(records[-1]["run_id"])
    assert record["source_hash"] == rule_hash()
    assert record["result"]["events"]
    assert record["result"]["metadata"]["run_fingerprint"]
    assert json.loads(output.read_text())["result"]["metadata"]["execution"] == "offline"
    assert output.with_suffix(".csv").exists()
    assert main(["report", records[-1]["run_id"], "--database", str(db),
                 "--output", str(output)]) == 0
    assert main(["walk-forward", *base, "--warmup-days", "30", "--fold-days", "30",
                 "--output", str(tmp_path / "fold.json")]) == 0
    assert len(list(tmp_path.glob("fold-*.json"))) >= 2


def test_cli_fail_closed(tmp_path: Path) -> None:
    assert main(["demo", str(tmp_path)]) == 0
    assert main(["demo", str(tmp_path)]) == 2
    config = tmp_path / "live.toml"
    config.write_text('[execution]\nmode="live"\n')
    assert main(["backtest", str(tmp_path / "bars.csv"), "--manifest",
                 str(tmp_path / "data.toml"), "--config", str(config)]) == 2


def test_config_overrides_without_dataset(tmp_path: Path, capsys) -> None:
    config = tmp_path / "config.toml"
    config.write_text('schema_version=2\n[strategy]\nentry_strategy="turtle_s1"\n')
    assert main(["backtest", "--config", str(config), "--print-config",
                 "--set", "strategy.entry_length=30"]) == 0
    resolved = json.loads(capsys.readouterr().out)
    assert resolved["strategy"]["entry_length"] == 30
    assert resolved["strategy"]["exit_length"] == 10
    assert main(["backtest", "--print-config", "--set", "strategy.allow_short=1"]) == 2
    assert json.loads(capsys.readouterr().err)["level"] == "error"
    assert main(["backtest", "--print-config", "--set", "risk.unit_fraction=0.01",
                 "--set", "risk.unit_fraction=0.02"]) == 2


def test_missing_arguments_are_json_errors(capsys) -> None:
    assert main(["backtest"]) == 2
    assert json.loads(capsys.readouterr().err)["level"] == "error"
    assert main(["backtest", "--unknown"]) == 2
    assert json.loads(capsys.readouterr().err)["level"] == "error"


def test_report_csv_cannot_overwrite_input_data(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    from turtle_trader import cli
    from turtle_trader.config import AppConfig
    from turtle_trader.data import Dataset

    source = tmp_path / "bars.csv"
    source.write_bytes(b"original input data\n")
    manifest = tmp_path / "manifest.toml"
    manifest.write_bytes(b"original manifest\n")
    # This regression exercises output preflight before engine execution; input
    # parsing and real-session completeness have separate integration coverage.
    dataset = Dataset([], SimpleNamespace(interval_minutes=5, price_basis="action_free"),
                      "data", [])
    monkeypatch.setattr(cli, "load_dataset", lambda *args, **kwargs: dataset)
    monkeypatch.setattr(cli, "load_config", lambda *args: AppConfig())
    assert main(["backtest", str(source), "--manifest", str(manifest),
                 "--output", str(tmp_path / "bars.json")]) == 2
    assert source.read_bytes() == b"original input data\n"
    assert manifest.read_bytes() == b"original manifest\n"
    assert not (tmp_path / "bars.json").exists()


def test_report_export_cannot_overwrite_benchmark(tmp_path: Path) -> None:
    benchmark = tmp_path / "benchmark.csv"
    benchmark.write_bytes(b"timestamp,price\n")
    assert main(["report", "unused", "--benchmark", str(benchmark),
                 "--database", str(tmp_path / "missing.sqlite"),
                 "--output", str(tmp_path / "benchmark.json")]) == 2
    assert benchmark.read_bytes() == b"timestamp,price\n"
    assert not (tmp_path / "missing.sqlite").exists()
