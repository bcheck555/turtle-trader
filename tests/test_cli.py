import base64
import hashlib
import json
from pathlib import Path

from turtle_trader.cli import main, source_hash
from turtle_trader.persistence import RunStore


def test_source_integrity() -> None:
    assert source_hash() == "fabcbb8584a68fd9552ad22f28fb98d5e9d2dfea06650a76ea4a11a415a04e23"
    root = Path(__file__).parents[1] / "reference"
    raw = base64.b64decode((root / "revelio.original.base64").read_bytes())
    assert hashlib.sha256(raw).hexdigest() == source_hash()


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
    assert record["source_hash"] == source_hash()
    assert len(record["result"]["events"]) == 70
    assert json.loads(output.read_text())["result"]["warnings"]
    assert json.loads(output.read_text())["result"]["metadata"]["execution"] == "offline"
    assert output.with_suffix(".csv").exists()
    assert main(["report", records[-1]["run_id"], "--database", str(db),
                 "--output", str(output)]) == 0
    assert main(["walk-forward", *base, "--warmup-days", "30", "--fold-days", "7",
                 "--output", str(tmp_path / "fold.json")]) == 0
    assert len(list(tmp_path.glob("fold-*.json"))) >= 2


def test_cli_fail_closed(tmp_path: Path) -> None:
    assert main(["demo", str(tmp_path)]) == 0
    assert main(["demo", str(tmp_path)]) == 2
    config = tmp_path / "live.toml"
    config.write_text('[execution]\nmode="live"\n')
    assert main(["backtest", str(tmp_path / "bars.csv"), "--manifest",
                 str(tmp_path / "data.toml"), "--config", str(config)]) == 2
