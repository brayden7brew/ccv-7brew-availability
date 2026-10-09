"""Weekly availability and deterministic WIW recurrence planning.

Stored days remain Sunday–Saturday for backward compatibility. The UI presents
Monday–Sunday; new timed entries specify unavailable hours, while historical
"hours" entries retain their original available-hours meaning.
"""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from .config import settings
from .models import WeeklySchedule

DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

class UnavailableRange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start: str
    end: str

    @model_validator(mode='after')
    def valid(self):
        allowed = {f'{m//60:02d}:{m%60:02d}' for m in range(300, 1381, 15)}
        if self.start not in allowed or self.end not in allowed or self.end <= self.start:
            raise ValueError('Choose unavailable From and To times between 5 AM and 11 PM in 15-minute steps, with To later than From.')
        return self


class DayHours(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['hours', 'unavailable', 'all_day', 'none'] = 'none'
    start: str = ''
    end: str = ''
    ranges: list[UnavailableRange] = Field(default_factory=list, max_length=6)

    @model_validator(mode='after')
    def valid(self):
        if self.mode == 'unavailable' and self.ranges:
            spans = sorted(self.ranges, key=lambda r: r.start)
            if any(a.end > b.start for a, b in zip(spans, spans[1:])):
                raise ValueError('Unavailable periods must not overlap. Adjust or remove the overlapping period.')
            self.ranges = spans
            self.start = self.end = ''
            return self
        if self.mode != 'unavailable':
            self.ranges = []
        if self.mode not in ('hours', 'unavailable'):
            self.start = self.end = ''
            return self
        try:
            for v in (self.start, self.end):
                if len(v) != 5 or v[2] != ':': raise ValueError()
                time.fromisoformat(v)
            if end_minutes(self.end) <= minutes(self.start): raise ValueError()
        except ValueError:
            raise ValueError('Enter a From and To time, with To later that same day. Split overnight hours across the two days.')
        return self

class WeeklyInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    effective_date: date
    days: list[DayHours] = Field(min_length=7, max_length=7)

    @model_validator(mode='after')
    def valid(self):
        today = local_today()
        if self.effective_date < today + timedelta(days=1):
            raise ValueError('Choose a future start date that meets the notice period.')
        if self.effective_date > today + timedelta(days=366):
            raise ValueError('Choose a start date within the next year.')
        return self

def local_today():
    return datetime.now(ZoneInfo(settings().business_timezone)).date()

def minutes(value):
    h, m = map(int, value.split(':'))
    return h*60+m

def timeline(db, employee_id, dry_run):
    rows = db.scalars(select(WeeklySchedule).where(WeeklySchedule.employee_id == employee_id,
        WeeklySchedule.dry_run == dry_run).order_by(WeeklySchedule.id)).all()
    # A later approved request on the same date supersedes the earlier version.
    by_date = {r.effective_date: r for r in rows}
    return sorted(by_date.values(), key=lambda r:r.effective_date)

def timeline_ids(rows):
    return [r.id for r in rows]

def display_schedule(row):
    return {'effective_date': row.effective_date.isoformat(), 'days': row.days,
            'change_id': row.change_id} if row else None

def at(day, minute):
    naive = datetime.combine(day, time()) + timedelta(minutes=minute)
    zone = ZoneInfo(settings().business_timezone)
    aware = naive.replace(tzinfo=zone)
    if aware.utcoffset() != aware.replace(fold=1).utcoffset():
        raise ValueError('An event boundary falls in a daylight-saving clock change. Adjust the hours for this request.')
    return aware

def end_minutes(value):
    return 1440 if value == '00:00' else minutes(value)

def blocked(day):
    if day['mode'] == 'all_day': return []
    if day['mode'] == 'none': return [(0, 1440)]
    if day['mode'] == 'unavailable' and day.get('ranges'):
        merged = []
        for span in sorted(day['ranges'], key=lambda r: r['start']):
            start, end = minutes(span['start']), minutes(span['end'])
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        return merged
    start, end = minutes(day['start']), end_minutes(day['end'])
    if day['mode'] == 'unavailable': return [(start, end)]
    return [(a,b) for a,b in [(0,start),(end,1440)] if a < b]

def split_dst_anchor(payload):
    """Keep a clock-change all-day occurrence outside the recurring rule.

    WIW rejects adjacent weekly rules when an all-day recurrence is anchored
    on the 25-hour fall-back day. A one-off plus next week's recurring rule
    preserves the local schedule. Apply the same representation to spring.
    """
    if not payload.get('all_day') or not payload.get('recurrence'):
        return [dict(payload)]
    zone = ZoneInfo(settings().business_timezone)
    start = datetime.fromisoformat(payload['start_time']).astimezone(zone)
    end = datetime.fromisoformat(payload['end_time']).astimezone(zone)
    if start.utcoffset() == end.utcoffset():
        return [dict(payload)]
    if start.time() != time() or end.time() != time() or end.date() != start.date() + timedelta(days=1):
        raise ValueError('Only a single full local day can be split at a clock change.')
    parts = payload['recurrence'].split(';')
    if (parts[0] != 'FREQ=WEEKLY' or not all(p.startswith(('FREQ=', 'BYDAY=', 'COUNT=')) for p in parts)
            or sum(p.startswith('BYDAY=') for p in parts) != 1):
        raise ValueError('Unsupported clock-change recurrence.')
    once = dict(payload)
    once.pop('recurrence')
    count = next((int(p[6:]) for p in parts if p.startswith('COUNT=')), None)
    if count is not None and count <= 0:
        raise ValueError('Invalid recurrence count.')
    if count == 1:
        return [once]
    later = {**payload, 'start_time':(start + timedelta(days=7)).isoformat(),
             'end_time':(end + timedelta(days=7)).isoformat()}
    if count is not None:
        later['recurrence'] = ';'.join(f'COUNT={count-1}' if p.startswith('COUNT=') else p for p in parts)
    return [once, later]


def event_plan(profiles, today=None):
    """Rebuild only portal-managed rules from today's local midnight onward.

    Earlier periods stay in the immutable portal audit. No PUT of a past DTSTART:
    WIW documents a 24-hour lower bound for start_time. Weekly COUNT caps each old
    rule immediately before the next approved effective date; latest has no cap.
    """
    today = today or local_today()
    profiles = sorted(profiles, key=lambda p: p['effective_date'])
    result = []
    for i, profile in enumerate(profiles):
        begin = max(today, date.fromisoformat(profile['effective_date']))
        end = date.fromisoformat(profiles[i+1]['effective_date']) if i+1 < len(profiles) else None
        if end and begin >= end: continue
        for weekday, day in enumerate(profile['days']):
            first = begin + timedelta(days=(weekday-(begin.weekday()+1)%7)%7)
            if end and first >= end: continue
            rule = 'FREQ=WEEKLY;BYDAY=' + ('SU', 'MO', 'TU', 'WE', 'TH', 'FR', 'SA')[weekday]
            if end:
                count = ((end-timedelta(days=1)-first).days//7)+1
                rule += f';COUNT={count}'
            for a,b in blocked(day):
                result.extend(split_dst_anchor({'type':1, 'start_time':at(first,a).isoformat(),
                    'end_time':at(first,b).isoformat(), 'all_day':a==0 and b==1440,
                    'notes':'Weekly hours approved in the availability portal', 'recurrence':rule}))
    return result


def working_windows(day):
    """Available intervals clipped to the 5 AM–11 PM working period."""
    cursor = 300
    result = []
    for start, end in sorted(blocked(day)):
        start, end = max(300, start), min(1380, end)
        if end <= start:
            continue
        if start > cursor:
            result.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < 1380:
        result.append((cursor, 1380))
    return result


def working_hour_labels(day):
    return [(f'{start//60:02d}:{start%60:02d}', f'{end//60:02d}:{end%60:02d}')
            for start, end in working_windows(day)]


def week_totals(schedule):
    available = sum(end-start for day in schedule['days'] for start, end in working_windows(day))
    def label(value):
        hours, mins = divmod(value, 60)
        return f'{hours}h' + (f' {mins}m' if mins else '')
    return {'available': label(available), 'unavailable': label(7*1080-available)}


def unavailable_hour_labels(day):
    return [(f'{max(start,300)//60:02d}:{max(start,300)%60:02d}',
             f'{min(end,1380)//60:02d}:{min(end,1380)%60:02d}')
            for start, end in blocked(day) if max(start,300) < min(end,1380)]
