"""Readable dates and times for portal pages; stored values remain unchanged."""
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo
from .config import settings


def clock_label(value):
    try:
        parsed = time.fromisoformat(str(value))
        return f"{parsed.hour % 12 or 12}:{parsed.minute:02d} {'AM' if parsed.hour < 12 else 'PM'}"
    except (TypeError, ValueError):
        return str(value)


def date_label(value):
    try:
        parsed = date.fromisoformat(str(value)) if not isinstance(value, date) else value
        return f'{parsed:%b} {parsed.day}, {parsed.year}'
    except (TypeError, ValueError):
        return str(value)


def local_datetime(value):
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        local = parsed.astimezone(ZoneInfo(settings().business_timezone))
        return f'{date_label(local)} at {clock_label(local.time())} {local:%Z}'
    except (AttributeError, TypeError, ValueError):
        return str(value)
