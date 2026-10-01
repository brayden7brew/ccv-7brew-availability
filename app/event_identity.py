"""Conservative comparisons of saved WIW availability and approved payloads."""
from datetime import timedelta, timezone, time
from zoneinfo import ZoneInfo
from dateutil.parser import parse
from .config import settings


def event_signature(event):
    start, end = (parse(event[key]) for key in ('start_time', 'end_time'))
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise ValueError('Availability timestamps must have offsets and a positive duration.')
    all_day = event.get('all_day', False)
    if not isinstance(all_day, bool) or type(event['type']) is not int:
        raise ValueError('Invalid availability type.')
    if all_day:
        zone = ZoneInfo(settings().business_timezone)
        local_start, local_end = start.astimezone(zone), end.astimezone(zone)
        # WIW uses an inclusive 23:59:59 end for full local days. Normalize
        # only that representation; never round timed events or shifted dates.
        if (local_start.time() == time() and local_end.time() == time(23,59,59)
                and local_end.date() == local_start.date()):
            end += timedelta(seconds=1)
    rule = event.get('recurrence') or ''
    if not isinstance(rule, str): raise ValueError('Invalid recurrence.')
    return (event['type'], start.astimezone(timezone.utc).isoformat(),
            end.astimezone(timezone.utc).isoformat(), all_day,
            tuple(sorted(rule.removeprefix('RRULE:').upper().split(';'))))
