"""Sourced US market-wide pauses; never infer a halt from an absent candle.

NYSE MWCB Working Group report, March 31, 2021, printed pages 4–5:
https://www.nyse.com/publicdocs/nyse/markets/nyse/Report_of_the_Market-Wide_Circuit_Breaker_Working_Group.pdf

Only bars wholly contained in a verified pause are exempt from coverage. A bar
containing any tradable seconds is still required, including reopening candles.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

SOURCE_URL = (
    "https://www.nyse.com/publicdocs/nyse/markets/nyse/"
    "Report_of_the_Market-Wide_Circuit_Breaker_Working_Group.pdf"
)
POLICY_VERSION = "nyse-mwcb-2020-v1"


@dataclass(frozen=True)
class MarketHalt:
    start: datetime
    end: datetime


MARKET_HALTS = (
    MarketHalt(datetime(2020, 3, 9, 13, 34, 13, tzinfo=UTC),
               datetime(2020, 3, 9, 13, 49, 13, tzinfo=UTC)),
    MarketHalt(datetime(2020, 3, 12, 13, 35, 44, tzinfo=UTC),
               datetime(2020, 3, 12, 13, 50, 44, tzinfo=UTC)),
    MarketHalt(datetime(2020, 3, 16, 13, 30, 1, tzinfo=UTC),
               datetime(2020, 3, 16, 13, 45, 1, tzinfo=UTC)),
    MarketHalt(datetime(2020, 3, 18, 16, 56, 17, tzinfo=UTC),
               datetime(2020, 3, 18, 17, 11, 17, tzinfo=UTC)),
)
_BY_DATE = {halt.start.date(): halt for halt in MARKET_HALTS}


def fully_halted(at: datetime, delta: timedelta) -> bool:
    """Whether the entire half-open candle interval has no tradable seconds."""
    if at.utcoffset() is None:
        raise ValueError("halt timestamps must be timezone-aware")
    if delta <= timedelta(0):
        raise ValueError("bar interval must be positive")
    halt = _BY_DATE.get(at.astimezone(UTC).date())
    return halt is not None and halt.start <= at and at + delta <= halt.end


def next_tradable_at(at: datetime) -> datetime:
    """Earliest permitted execution time for an observed candle's opening price.

    Minute labels can precede reopening by seconds. This establishes a lower
    bound, not a claim that the security's first actual trade occurred exactly
    at the exchange-wide reopening instant.
    """
    if at.utcoffset() is None:
        raise ValueError("halt timestamps must be timezone-aware")
    halt = _BY_DATE.get(at.astimezone(UTC).date())
    return halt.end if halt is not None and halt.start <= at < halt.end else at
