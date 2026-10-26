"""Timezone helpers; exchange calendars define session boundaries."""

from datetime import datetime
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")


def local_time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must have an explicit timezone")
    return value.astimezone(NEW_YORK)
