"""Earliest available session discovery and private credential loading."""

import importlib.util
from datetime import date
from pathlib import Path

import httpx
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "research/download_aapl_history.py"
SPEC = importlib.util.spec_from_file_location("aapl_download", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_discovery_ignores_holiday_and_out_of_hours_observations() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.params["sort"] == "asc"
        assert request.url.params["start"].startswith("1990")
        return httpx.Response(200, json={"bars": {"AAPL": [
            {"t": "2016-01-01T00:00:00Z"}, {"t": "2016-01-04T13:30:00Z"},
            {"t": "2016-01-04T14:30:00Z"},
        ]}, "next_page_token": None})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        start, discovery = module.discover_start(client, date(2026, 10, 26))
    assert start == date(2016, 1, 4)
    assert discovery["first_regular_timestamp"] == "2016-01-04T14:30:00+00:00"


def test_empty_discovery_page_continues_and_repeated_token_fails() -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"bars": {}, "next_page_token": "repeat"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ValueError, match="no first regular"):
            module.discover_start(client, date(2026, 10, 26))
    assert len(calls) == 2


def test_dotenv_never_executes_values_or_overwrites_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APCA_API_KEY_ID", "already-set")
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    path = tmp_path / ".env"
    path.write_text("APCA_API_KEY_ID=discarded\n"
                    "APCA_API_SECRET_KEY='$(never-run)'\nUNRELATED=value\n")
    module.load_credentials(path)
    assert module.os.environ["APCA_API_KEY_ID"] == "already-set"
    assert module.os.environ["APCA_API_SECRET_KEY"] == "$(never-run)"


def test_error_response_does_not_expose_provider_body() -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(
        403, json={"message": "private body"},
    ))) as client:
        with pytest.raises(ValueError, match="^historical discovery HTTP 403$"):
            module.discover_start(client, date(2026, 10, 26))
