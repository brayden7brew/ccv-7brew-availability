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


def finite_occurrences(events, before=None):
    """Compare complete finite series, or their prefix before a dated handover.

    Expand in business local time, preserving DST and duplicate occurrences.
    Never sample an infinite tail or round a timed interval.
    """
    from collections import Counter
    from dateutil.rrule import rrulestr
    result = Counter()
    zone = ZoneInfo(settings().business_timezone)
    for event in events:
        signature = event_signature(event)
        start = parse(signature[1]).astimezone(zone)
        end = parse(signature[2]).astimezone(zone)
        duration = end - start
        rule = event.get('recurrence') or ''
        if rule:
            parts = rule.removeprefix('RRULE:').upper().split(';')
            if before is None and not any(p.startswith(('COUNT=', 'UNTIL=')) for p in parts):
                raise ValueError('Unbounded series cannot confirm a finite handover.')
            occurrences = rrulestr(rule, dtstart=start)
        else:
            occurrences = [start]
        for index, occurrence in enumerate(occurrences):
            if index >= 10000:
                raise ValueError('Series too large to verify safely.')
            if before is not None and occurrence >= before:
                break
            finish = occurrence + duration
            if before is not None:
                finish = min(finish, before)
            result[(signature[0], occurrence.astimezone(timezone.utc).isoformat(),
                    finish.astimezone(timezone.utc).isoformat(), signature[3],
                    event.get('notes') or '')] += 1
    return result


def verify_finite_replacement(events, payload):
    if finite_occurrences(events) != finite_occurrences([payload]):
        raise ValueError('Split availability does not preserve the complete approved date range.')


def in_read_window(event, first, last):
    begin, finish = parse(first), parse(last)
    zone = ZoneInfo(settings().business_timezone)
    if begin.tzinfo is None: begin = begin.replace(tzinfo=zone)
    if finish.tzinfo is None: finish = finish.replace(tzinfo=zone)
    return any(parse(key[1]) < finish and parse(key[2]) > begin
               for key in finite_occurrences([event], before=finish))
