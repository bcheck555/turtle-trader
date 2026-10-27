from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest

from turtle_trader.halts import MARKET_HALTS, fully_halted, next_tradable_at


@pytest.mark.parametrize("halt", MARKET_HALTS)
def test_verified_pauses_exempt_only_fourteen_complete_minutes(halt):
    delta = timedelta(minutes=1)
    first_label = halt.start.replace(second=0)
    slots = [first_label + i * delta for i in range(16)]
    excluded = [at for at in slots if fully_halted(at, delta)]
    assert halt.end - halt.start == timedelta(minutes=15)
    assert len(excluded) == 14
    assert excluded[0] == first_label + delta
    assert not fully_halted(first_label, delta)
    assert not fully_halted(halt.end.replace(second=0), delta)
    ny = ZoneInfo("America/New_York")
    assert fully_halted(excluded[0].astimezone(ny), delta)
    assert next_tradable_at(halt.end.replace(second=0).astimezone(ny)) == halt.end
    assert next_tradable_at(halt.start) == halt.end
    assert next_tradable_at(halt.end) == halt.end
    assert next_tradable_at(first_label) == first_label


def test_halt_helpers_reject_naive_times_and_nonpositive_intervals():
    at = MARKET_HALTS[0].start
    with pytest.raises(ValueError, match="timezone-aware"):
        fully_halted(at.replace(tzinfo=None), timedelta(minutes=1))
    with pytest.raises(ValueError, match="timezone-aware"):
        next_tradable_at(at.replace(tzinfo=None))
    for delta in (timedelta(0), timedelta(minutes=-1)):
        with pytest.raises(ValueError, match="positive"):
            fully_halted(at, delta)
