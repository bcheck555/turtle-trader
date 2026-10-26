import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from turtle_trader import cli
from turtle_trader.alpaca import download_alpaca
from turtle_trader.data import load_dataset
from turtle_trader.persistence import RunStore

NOW = datetime(2026, 1, 8, 12, tzinfo=UTC)


def args(directory: Path) -> list[str]:
    return ["download", "alpaca", "--symbols", "SPY", "QQQ", "--start", "2026-01-05",
            "--end", "2026-01-05", "--output", str(directory)]


def bars(symbol: str, *, complete: bool = True) -> list[dict[str, object]]:
    start = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    return [{"t": (start + timedelta(minutes=i * 5)).isoformat(), "o": 100,
             "h": 102, "l": 99, "c": 101, "v": 1000}
            for i in range(78 if complete else 1)]


def install_mock(monkeypatch: pytest.MonkeyPatch, *, complete: bool = True,
                 status: int = 200) -> list[httpx.Request]:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if status != 200:
            return httpx.Response(status, json={"message": "untrusted-server-secret"})
        return httpx.Response(200, json={"bars": {s: bars(s, complete=complete)
                                                 for s in ("SPY", "QQQ")},
                                         "next_page_token": None})

    monkeypatch.setattr(cli, "_alpaca_client", lambda: httpx.Client(
        transport=httpx.MockTransport(respond), headers={"APCA-API-KEY-ID": "test-only-key",
                                                        "APCA-API-SECRET-KEY": "test-only-secret"}))

    def fixed_download(request: Any, output: Path, *, client: httpx.Client) -> Any:
        return download_alpaca(request, output, client=client, now=NOW, sleep=lambda _: None)

    monkeypatch.setattr(cli, "download_alpaca", fixed_download)
    return requests


def test_complete_download_roundtrip_and_saved_research_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = install_mock(monkeypatch)
    output = tmp_path / "dataset"
    assert cli.main(args(output)) == 0
    dataset = load_dataset(output / "bars.csv", output / "manifest.toml")
    assert len(dataset.bars) == 156
    assert dataset.manifest.price_basis == "adjusted_research"
    assert dataset.manifest.adjustment == "all" and not dataset.manifest.action_free
    assert not dataset.manifest.synthetic
    assert len(requests) == 1
    assert requests[0].url.host == "data.alpaca.markets"
    assert requests[0].url.params["asof"] == "2026-01-05"
    database = tmp_path / "runs.sqlite"
    report = tmp_path / "report.json"
    base = [str(output / "bars.csv"), "--manifest", str(output / "manifest.toml"),
            "--database", str(database)]
    for command in ("validate", "import", "replay"):
        assert cli.main([command, *base]) == 0
    assert cli.main(["backtest", *base, "--output", str(report)]) == 0
    store = RunStore(database)
    for run in store.list_runs():
        result = store.load(run["run_id"])["result"]
        assert result["price_basis"] == "adjusted_research"
        assert result["research_warnings"]
        assert result["metadata"]["price_basis"] == "adjusted_research"
    last = store.list_runs()[-1]
    assert cli.main(["report", last["run_id"], "--database", str(database),
                     "--output", str(report)]) == 0
    payload = json.loads(report.read_text())
    assert payload["summary"]["price_basis"] == "adjusted_research"
    assert payload["summary"]["research_warnings"]
    assert "adjusted_research" in report.with_suffix(".csv").read_text()


def test_gaps_preserve_files_return_three_and_strict_backtest_rejects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_mock(monkeypatch, complete=False)
    output = tmp_path / "dataset"
    assert cli.main(args(output)) == 3
    assert all((output / name).is_file() for name in (
        "bars.csv", "manifest.toml", "coverage.json", "provenance.json"))
    with pytest.raises(ValueError, match="missing candles"):
        load_dataset(output / "bars.csv", output / "manifest.toml")
    assert len(load_dataset(output / "bars.csv", output / "manifest.toml", strict=False).bars) == 2
    assert cli.main(["backtest", str(output / "bars.csv"), "--manifest",
                     str(output / "manifest.toml")]) == 2


def test_failure_redacts_credentials_and_has_no_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    requests = install_mock(monkeypatch, status=403)
    output = tmp_path / "dataset"
    assert cli.main(args(output)) == 2
    assert len(requests) == 1
    assert (output / "error.json").is_file()
    assert not (output / "bars.csv").exists()
    assert not (output / "manifest.toml").exists()
    captured = capsys.readouterr()
    text = captured.out + captured.err + (output / "error.json").read_text()
    assert not any(s in text for s in (
        "test-only-key", "test-only-secret", "untrusted-server-secret", "APCA-API-SECRET-KEY"))


def test_missing_credentials_and_non_download_commands_do_not_read_host_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Replace the mapping rather than reading/preserving any actual credential values.
    monkeypatch.setattr(cli.os, "environ", {})
    assert cli.main(args(tmp_path / "missing")) == 2
    assert not (tmp_path / "missing").exists()
    assert cli.main(["demo", str(tmp_path / "demo")]) == 0


def test_client_credentials_are_environment_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.os, "environ", {
        "APCA_API_KEY_ID": "fake-key", "APCA_API_SECRET_KEY": "fake-secret"})
    with cli._alpaca_client() as client:
        assert client.headers["APCA-API-KEY-ID"] == "fake-key"
        assert client.headers["APCA-API-SECRET-KEY"] == "fake-secret"
        assert client.follow_redirects is False
    monkeypatch.setattr(cli.os, "environ", {
        "APCA_API_KEY_ID": "fake\nkey", "APCA_API_SECRET_KEY": "fake-secret"})
    with pytest.raises(ValueError, match="format invalid"):
        cli._alpaca_client()


def test_cli_regenerates_config_only_research_labels(tmp_path: Path) -> None:
    database = tmp_path / "runs.sqlite"
    output = tmp_path / "config-only.json"
    run_id = RunStore(database).save(
        "backtest", {"price_basis": "adjusted_research"}, "data",
        {"equity": [], "fills": []}, "source")
    assert cli.main(["report", run_id, "--database", str(database),
                     "--output", str(output)]) == 0
    assert json.loads(output.read_text())["summary"]["price_basis"] == "adjusted_research"
    assert "adjusted_research" in output.with_suffix(".csv").read_text()


def test_walkforward_reports_preserve_research_basis(tmp_path: Path) -> None:
    demo = tmp_path / "demo"
    assert cli.main(["demo", str(demo)]) == 0
    manifest = demo / "data.toml"
    manifest.write_text(manifest.read_text().replace('adjustment="split"', 'adjustment="all"')
                        .replace('action_free=true', 'action_free=false')
                        + 'price_basis="adjusted_research"\n')
    assert cli.main(["walk-forward", str(demo / "bars.csv"), "--manifest", str(manifest),
                     "--database", str(tmp_path / "folds.sqlite"), "--warmup-days", "30",
                     "--fold-days", "7", "--output", str(tmp_path / "fold.json")]) == 0
    reports = list(tmp_path.glob("fold-*.json"))
    assert len(reports) >= 2
    for report in reports:
        payload = json.loads(report.read_text())
        assert payload["summary"]["price_basis"] == "adjusted_research"
        assert payload["result"]["price_basis"] == "adjusted_research"
        assert payload["research_warnings"]
