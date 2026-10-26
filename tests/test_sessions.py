from datetime import UTC, datetime

import pytest

from turtle_trader.sessions import local_time


def test_timezone_requires_explicit_offset_and_follows_dst() -> None:
    with pytest.raises(ValueError, match="timezone"):
        local_time(datetime(2026, 1, 1))
    assert local_time(datetime(2026, 1, 5, 14, 30, tzinfo=UTC)).hour == 9
    assert local_time(datetime(2026, 7, 6, 13, 30, tzinfo=UTC)).hour == 9
