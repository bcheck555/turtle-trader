import csv
import hashlib
import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from email.utils import format_datetime

import httpx
import pytest

from turtle_trader import alpaca
from turtle_trader.alpaca import DownloadRequest, download_alpaca
from turtle_trader.data import ExchangeCalendar, read_manifest

DAY = date(2025, 1, 2)
NOW = datetime(2025, 1, 3, 12, tzinfo=UTC)
REQUEST = DownloadRequest(("A",), DAY, DAY)
SECRET = "private-key-value-do-not-write"


def raw(at="2025-01-02T14:30:00Z", **changes):
    return {"t": at, "o": 100, "h": 101, "l": 99, "c": 100, "v": 100} | changes


def session_rows(day=DAY, coverage="regular", symbol="A"):
    bounds = ExchangeCalendar(day, day).bounds(day, coverage)
    assert bounds is not None
    rows = []
    at = bounds[0]
    while at < bounds[1]:
        rows.append(raw(at.isoformat().replace("+00:00", "Z")))
        at += timedelta(minutes=5)
    return {symbol: rows}


def page(bars=None, token=None):
    return httpx.Response(200, json={"bars": bars or {}, "next_page_token": token})


def run(tmp_path, handler, request=REQUEST, now=NOW, monotonic=lambda: 0):
    delays = []
    with httpx.Client(transport=httpx.MockTransport(handler),
                      headers={"APCA-API-KEY-ID": SECRET,
                               "APCA-API-SECRET-KEY": SECRET}) as client:
        result = download_alpaca(request, tmp_path / "download", client=client,
                                 now=now, sleep=delays.append, monotonic=monotonic)
    return result, delays


def assert_error_only(result, code=None):
    assert result.status == "failed"
    assert result.exit_code == 2
    assert {p.name for p in result.output.iterdir()} == {"error.json"}
    text = (result.output / "error.json").read_text()
    assert SECRET not in text
    assert "APCA-API" not in text
    if code is not None:
        assert result.error_code == code
        assert json.loads(text)["error_code"] == code
    assert not list(result.output.parent.glob(".*.staging-*"))


def test_complete_paginated_download_has_deterministic_adjusted_artifacts(tmp_path):
    rows = session_rows()["A"]
    requests = []

    def handler(request):
        requests.append(request)
        token = request.url.params.get("page_token")
        if token is None:
            return page({"A": rows[:2]}, "empty-next")
        if token == "empty-next":
            return page({}, "last")
        return page({"A": rows[2:]})

    result, delays = run(tmp_path, handler)
    assert result.status == "ready_for_adjusted_research"
    assert result.exit_code == 0
    assert result.pages == 3
    assert result.rows_written == result.rows_received == 78
    assert result.missing_count == 0
    assert delays == []
    assert {p.name for p in result.output.iterdir()} == {
        "bars.csv", "manifest.toml", "coverage.json", "provenance.json"}
    for request in requests:
        assert str(request.url).startswith(alpaca.ENDPOINT + "?")
        assert request.method == "GET"
        assert request.url.params["limit"] == "10000"
        assert request.url.params["sort"] == "asc"
        assert request.url.params["adjustment"] == "all"
        assert request.url.params["asof"] == "2025-01-02"
        assert request.url.params["feed"] == "sip"
        assert request.url.params["start"] == "2025-01-02T14:30:00Z"
        assert request.url.params["end"] == "2025-01-02T21:00:00Z"
        assert request.extensions["timeout"]["connect"] == 10
        assert request.extensions["timeout"]["read"] == 30
    manifest = read_manifest(result.output / "manifest.toml")
    assert manifest.price_basis == "adjusted_research"
    assert manifest.adjustment == "all"
    assert manifest.action_free is manifest.synthetic is False
    provenance = json.loads((result.output / "provenance.json").read_text())
    assert provenance["warnings"]
    assert provenance["versions"]["httpx"] == httpx.__version__
    assert provenance["query_parameters"] == {
        "timeframe": "5Min", "asof": "2025-01-02", "adjustment": "all",
        "limit": 10000, "sort": "asc", "feed": "sip"}
    for name, digest in result.checksums.items():
        assert hashlib.sha256((result.output / name).read_bytes()).hexdigest() == digest
    for name, digest in provenance["artifact_checksums"].items():
        assert digest == result.checksums[name]
    assert SECRET not in "".join(p.read_text() for p in result.output.iterdir())


def test_sorting_deduplicates_identical_values_and_excludes_out_of_hours(tmp_path):
    request = replace(REQUEST, symbols=("B", "A"))
    result, _ = run(tmp_path, lambda _: page({
        "B": [raw("2025-01-02T14:35:00Z"), raw()],
        "A": [raw("2025-01-02T14:25:00Z"), raw(), raw(o=100.0)]}), request)
    assert result.status == "coverage_incomplete"
    assert result.exit_code == 3
    assert result.rows_received == 5
    assert result.rows_written == 3
    assert result.duplicates == result.excluded_out_of_hours == 1
    with (result.output / "bars.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert [(r["timestamp"], r["symbol"]) for r in rows] == [
        ("2025-01-02T14:30:00Z", "A"), ("2025-01-02T14:30:00Z", "B"),
        ("2025-01-02T14:35:00Z", "B")]
    coverage = json.loads((result.output / "coverage.json").read_text())
    assert coverage["missing_count"] == 153
    assert coverage["complete"] is False
    assert coverage["excluded_out_of_hours"] == 1


def test_empty_and_short_pages_follow_token_and_repeated_token_fails(tmp_path):
    responses = iter([page({"A": [raw()]}, "same"), page({}, "same")])
    result, _ = run(tmp_path, lambda _: next(responses))
    assert_error_only(result, "repeated_page_token")
    assert result.pages == 2


def test_conflicting_duplicate_discards_all_staged_rows(tmp_path):
    responses = iter([page({"A": [raw()]}, "next"), page({"A": [raw(c=100.5)]})])
    result, _ = run(tmp_path, lambda _: next(responses))
    assert_error_only(result, "conflicting_duplicate")


def test_retries_network_429_http_date_and_503_without_feed_fallback(tmp_path):
    attempts = []

    def handler(request):
        attempts.append(request)
        if len(attempts) == 1:
            raise httpx.ConnectError(SECRET, request=request)
        if len(attempts) == 2:
            return httpx.Response(429, headers={"Retry-After": format_datetime(
                NOW + timedelta(seconds=5), usegmt=True)}, text=SECRET)
        if len(attempts) == 3:
            return httpx.Response(503, text=SECRET)
        return page(session_rows())

    result, delays = run(tmp_path, handler)
    assert result.exit_code == 0
    assert delays == [1, 5, 4]
    assert {r.url.params["feed"] for r in attempts} == {"sip"}
    assert {str(r.url) for r in attempts} == {str(attempts[0].url)}
    provenance = json.loads((result.output / "provenance.json").read_text())
    assert provenance["counts"]["retries"] == 3


@pytest.mark.parametrize("retry_after", ["61", "Infinity", "bad-header"])
def test_retry_after_is_bounded_and_invalid_values_never_leak(tmp_path, retry_after):
    attempts = []

    def handler(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"Retry-After": retry_after}, text=SECRET)
        return page()

    result, delays = run(tmp_path, handler)
    if retry_after == "61":
        assert_error_only(result, "retry_after_exceeds_60_seconds")
        assert delays == []
        assert len(attempts) == 1
    else:
        assert result.status == "coverage_incomplete"
        assert delays == [1]


@pytest.mark.parametrize("status", [401, 403, 404, 302])
def test_http_errors_and_redirects_never_echo_provider_body_or_headers(tmp_path, status):
    result, delays = run(tmp_path, lambda _: httpx.Response(
        status, text=SECRET, headers={"Location": "https://example.com/" + SECRET}))
    assert_error_only(result, f"http_status_{status}")
    assert delays == []


def test_network_failure_stops_after_three_retries_and_sanitizes_exception(tmp_path):
    attempts = []

    def handler(request):
        attempts.append(request)
        raise httpx.ReadTimeout(SECRET, request=request)

    result, delays = run(tmp_path, handler)
    assert_error_only(result, "network_retries_exhausted")
    assert len(attempts) == 4
    assert delays == [1, 2, 4]


def test_batches_are_sequential_and_at_most_fifty_symbols(tmp_path):
    batches = []

    def handler(request):
        batches.append(request.url.params["symbols"].split(","))
        return page()

    request = replace(REQUEST, symbols=tuple(f"S{i:03d}" for i in range(51)))
    result, _ = run(tmp_path, handler, request)
    assert [len(b) for b in batches] == [50, 1]
    assert result.pages == 2
    assert result.rows_written == 0


@pytest.mark.parametrize("day,coverage,opening,closing", [
    (date(2025, 3, 10), "regular", "13:30:00Z", "20:00:00Z"),
    (date(2025, 11, 28), "regular", "14:30:00Z", "18:00:00Z"),
    (date(2025, 11, 28), "extended", "09:00:00Z", "18:00:00Z"),
])
def test_dst_and_early_close_query_calendar_bounds(tmp_path, day, coverage, opening, closing):
    seen = []

    def handler(request):
        seen.append(request)
        return page(session_rows(day, coverage))

    request = replace(REQUEST, start=day, end=day, coverage=coverage)
    result, _ = run(tmp_path, handler, request, datetime(2026, 1, 1, tzinfo=UTC))
    assert result.exit_code == 0
    assert seen[0].url.params["start"] == f"{day.isoformat()}T{opening}"
    assert seen[0].url.params["end"] == f"{day.isoformat()}T{closing}"


@pytest.mark.parametrize("download_request,now,code", [
    (replace(REQUEST, start=date(2025, 1, 1), end=date(2025, 1, 1)), NOW,
     "no_exchange_sessions"),
    (REQUEST, datetime(2025, 1, 2, 23, tzinfo=UTC),
     "dates_must_precede_current_new_york_date"),
    (REQUEST, datetime(2025, 1, 3), "naive_acquisition_time"),
])
def test_invalid_date_windows_make_no_http_call(tmp_path, download_request, now, code):
    def handler(_):
        pytest.fail("invalid window must not call HTTP")

    result, _ = run(tmp_path, handler, download_request, now)
    assert_error_only(result, code)


@pytest.mark.parametrize("row,code", [
    (raw(o=0), "invalid_bar_price"),
    (raw(h="123"), "invalid_bar_price"),
    (raw(v=1.5), "invalid_bar_volume"),
    (raw(v=True), "invalid_bar_volume"),
    (raw(v=-1), "invalid_bar_volume"),
    (raw(l=102), "invalid_bar_values"),
    (raw("2025-01-02T14:30:00"), "invalid_bar_timestamp"),
    (raw("2025-01-02T14:31:00Z"), "invalid_bar_timestamp"),
    (raw("2024-01-02T14:30:00Z"), "invalid_bar_timestamp"),
])
def test_malformed_bars_fail_closed_without_partial_dataset(tmp_path, row, code):
    result, _ = run(tmp_path, lambda _: page({"A": [row]}))
    assert_error_only(result, code)


def test_malformed_json_and_missing_pagination_are_sanitized(tmp_path):
    result, _ = run(tmp_path, lambda _: httpx.Response(200, text=SECRET))
    assert_error_only(result, "invalid_json")


def test_absent_terminal_token_finishes_and_coverage_still_reports_gaps(tmp_path):
    result, _ = run(tmp_path, lambda _: httpx.Response(200, json={"bars": {}}))
    assert result.status == "coverage_incomplete"
    assert result.exit_code == 3
    assert result.missing_count == 78


def test_absent_terminal_token_accepts_complete_final_page(tmp_path):
    result, _ = run(tmp_path, lambda _: httpx.Response(200, json={"bars": session_rows()}))
    assert result.status == "ready_for_adjusted_research"
    assert result.exit_code == 0


def test_http_date_retry_after_uses_elapsed_monotonic_time_on_later_pages(tmp_path):
    clock = {"elapsed": 0}
    attempts = []

    def handler(request):
        attempts.append(request)
        if len(attempts) == 1:
            clock["elapsed"] = 55
            return page({}, "next")
        if len(attempts) == 2:
            return httpx.Response(429, headers={"Retry-After": format_datetime(
                NOW + timedelta(seconds=65), usegmt=True)})
        return page(session_rows())

    result, delays = run(tmp_path, handler, monotonic=lambda: clock["elapsed"])
    assert result.exit_code == 0
    assert result.pages == 2
    assert delays == [10]


def test_undeclared_provider_symbol_is_never_persisted(tmp_path):
    result, _ = run(tmp_path, lambda _: page({SECRET: [raw()]}))
    assert_error_only(result, "invalid_page_symbols")


def test_interruption_publishes_only_sanitized_error(tmp_path):
    def handler(_):
        raise KeyboardInterrupt(SECRET)

    result, _ = run(tmp_path, handler)
    assert_error_only(result, "download_failed")


def test_existing_destination_is_preserved_and_http_not_called(tmp_path):
    output = tmp_path / "download"
    output.mkdir()
    (output / "existing").write_text("keep")

    def handler(_):
        pytest.fail("existing destination must not call HTTP")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(FileExistsError):
            download_alpaca(REQUEST, output, client=client, now=NOW)
    assert (output / "existing").read_text() == "keep"
    assert {p.name for p in output.iterdir()} == {"existing"}


def test_atomic_publication_failure_removes_complete_artifacts_before_error_report(
        tmp_path, monkeypatch):
    actual_replace = alpaca.os.replace
    calls = []

    def fail_first(source, destination):
        calls.append((source, destination))
        if len(calls) == 1:
            raise OSError(SECRET)
        return actual_replace(source, destination)

    monkeypatch.setattr(alpaca.os, "replace", fail_first)
    result, _ = run(tmp_path, lambda _: page(session_rows()))
    assert_error_only(result, "download_failed")
    assert len(calls) == 2


def test_injected_client_does_not_read_credentials_from_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(alpaca.os, "environ", {
        "APCA_API_KEY_ID": SECRET, "APCA_API_SECRET_KEY": SECRET})

    def handler(request):
        assert "APCA-API-KEY-ID" not in request.headers
        assert "APCA-API-SECRET-KEY" not in request.headers
        return page()

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = download_alpaca(REQUEST, tmp_path / "download", client=client,
                                 now=NOW, sleep=lambda _: None)
    assert result.exit_code == 3


@pytest.mark.parametrize("changes", [
    {"symbols": ()}, {"symbols": ("A", "A")}, {"symbols": ("a",)},
    {"symbols": tuple(f"S{i}" for i in range(101))}, {"feed": "other"},
    {"coverage": "custom"}, {"interval_minutes": 4}, {"start": date(2026, 1, 1)},
])
def test_request_validation(changes):
    with pytest.raises(ValueError):
        replace(REQUEST, **changes)


def test_decimal_canonicalization_preserves_exact_source_precision():
    assert alpaca._number(Decimal("12345678901234567890123456789")) == (
        "12345678901234567890123456789")
    assert alpaca._number(Decimal("100.0100")) == "100.01"


def test_extreme_decimal_underflow_is_rejected_before_fixed_point_expansion():
    with pytest.raises(alpaca._DownloadError, match="invalid_bar_price"):
        alpaca._number(Decimal("1e-1000000000"))


def test_sub_float_precision_ohlc_violation_is_rejected_before_export(tmp_path):
    payload = ('{"bars":{"A":[{"t":"2025-01-02T14:30:00Z",'
               '"o":100.00000000000000001,"h":100,"l":99,"c":100,"v":100}]},'
               '"next_page_token":null}')
    result, _ = run(tmp_path, lambda _: httpx.Response(200, text=payload))
    assert_error_only(result, "invalid_bar_values")
