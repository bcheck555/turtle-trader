from datetime import UTC, datetime

import pytest

from turtle_trader.domain import StrategyConfig
from turtle_trader.sessions import NEW_YORK, allowed, deadline_for, session_start


def ny(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=NEW_YORK)


@pytest.mark.parametrize(("value", "expected"), [
    ("2026-01-01T16:59", "2025-12-31T17:00"),
    ("2026-01-01T17:00", "2026-01-01T17:00"),
    ("2026-03-08T03:00", "2026-03-07T17:00"),
    ("2026-11-01T02:00", "2026-10-31T17:00"),
])
def test_session_uses_open_and_calendar_rollover(value: str, expected: str) -> None:
    assert session_start(ny(value)) == ny(expected)
    assert session_start(ny(value).astimezone(UTC)) == ny(expected)


@pytest.mark.parametrize(("value", "expected"), [
    ("2026-01-05T16:29", True),
    ("2026-01-05T16:30", False),
    ("2026-01-05T17:59", False),
    ("2026-01-05T18:00", True),
    ("2026-01-09T18:00", False),
    ("2026-01-10T10:00", False),
    ("2026-01-11T17:59", False),
    ("2026-01-11T18:00", True),
    ("2026-01-12T02:00", True),
])
def test_allowed_exact_boundaries(value: str, expected: bool) -> None:
    assert allowed(ny(value), StrategyConfig()) is expected
    assert allowed(ny(value), StrategyConfig(day_trading=False))


@pytest.mark.parametrize(("value", "expected"), [
    ("2026-01-05T16:29", "2026-01-05T16:30"),
    ("2026-01-05T16:30", "2026-01-06T16:30"),
    ("2026-01-09T16:30", "2026-01-12T16:30"),
    ("2026-03-06T18:00", "2026-03-09T16:30"),
    ("2026-10-30T18:00", "2026-11-02T16:30"),
    ("2026-12-31T18:00", "2027-01-01T16:30"),
])
def test_deadline_calendar_weekends_and_dst(value: str, expected: str) -> None:
    result = deadline_for(ny(value), StrategyConfig())
    assert result == ny(expected)
    assert result.utcoffset() == ny(expected).utcoffset()


def test_naive_time_rejected() -> None:
    with pytest.raises(ValueError, match="timezone"):
        session_start(datetime(2026, 1, 1))


@pytest.mark.parametrize("minutes", [1, 2, 3, 5, 6, 10, 15, 30])
def test_all_pine_timeframes(minutes: int) -> None:
    assert StrategyConfig(interval_minutes=minutes).interval_minutes == minutes


@pytest.mark.parametrize("kwargs", [
    {"interval_minutes": 4}, {"interval_minutes": 60},
    {"close_hour": 19}, {"close_minute": 25, "interval_minutes": 30},
    {"open_minute": 5, "interval_minutes": 30},
])
def test_invalid_timeframes_and_schedule(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        StrategyConfig(**kwargs)
