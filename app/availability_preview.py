"""Read-only upcoming availability derived from WIW unavailable preferences."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from dateutil.parser import parse
from dateutil.rrule import rrulestr
from .config import settings


def preview(events, today):
    zone = ZoneInfo(settings().business_timezone)
    first = datetime.combine(today, time(), zone)
    last = first + timedelta(days=7)
    blocked = []
    for event in events:
        if event['type'] != 1: continue
        start, end = parse(event['start_time']), parse(event['end_time'])
        if start.tzinfo is None or end.tzinfo is None or end <= start: raise ValueError('Invalid dates')
        start, end = start.astimezone(zone), end.astimezone(zone)
        if event.get('all_day') and end.time() == time(23,59,59): end += timedelta(seconds=1)
        duration = end-start
        rule = event.get('recurrence') or ''
        occurrences = rrulestr(rule.removeprefix('RRULE:'), dtstart=start).between(first-duration,last,inc=True) if rule else [start]
        blocked.extend((value,value+duration) for value in occurrences if value < last and value+duration > first)
    rows=[]
    def label(value): return value.strftime('%I:%M %p').lstrip('0')
    for i in range(7):
        day=first+timedelta(days=i)
        begin,end=day.replace(hour=5),day.replace(hour=23)
        spans=sorted((max(a,begin),min(b,end)) for a,b in blocked if b>begin and a<end)
        cursor=begin; available=[]
        for a,b in spans:
            if a>cursor: available.append(f'{label(cursor)}–{label(a)}')
            cursor=max(cursor,b)
        if cursor<end: available.append(f'{label(cursor)}–{label(end)}')
        rows.append({'date':day.date(),'hours':', '.join(available) or 'No available hours'})
    return rows
