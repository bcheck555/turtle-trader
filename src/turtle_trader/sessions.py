"""New York calendar rules from the indicator, independent of exchange hours."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from turtle_trader.domain import StrategyConfig

NEW_YORK = ZoneInfo("America/New_York")


def local_time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("bar timestamps must have a timezone")
    return value.astimezone(NEW_YORK)


def session_start(value: datetime) -> datetime:
    """Return 17:00 NY starting the session containing the bar's open."""
    local = local_time(value)
    day = local.date() - timedelta(days=local.hour < 17)
    return datetime.combine(day, time(17), NEW_YORK)


def allowed(value: datetime, config: StrategyConfig) -> bool:
    """Pine's weekday/break filter; this deliberately is not an equity calendar."""
    if not config.day_trading:
        return True
    local = local_time(value)
    minute = local.hour * 60 + local.minute
    close = config.close_hour * 60 + config.close_minute
    opening = config.open_hour * 60 + config.open_minute
    return (
        (minute < close or minute >= opening)
        and local.weekday() != 5
        and not (local.weekday() == 6 and minute < opening)
        and not (local.weekday() == 4 and minute >= close)
    )


def deadline_for(value: datetime, config: StrategyConfig) -> datetime:
    """Next weekday close, using calendar dates so DST does not move the clock."""
    local = local_time(value)
    minute = local.hour * 60 + local.minute
    close = config.close_hour * 60 + config.close_minute
    day = local.date() + timedelta(days=minute >= close)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return datetime.combine(day, time(config.close_hour, config.close_minute), NEW_YORK)
