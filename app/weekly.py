"""Positive Sunday–Saturday availability and deterministic WIW recurrence planning."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from .config import settings
from .models import WeeklySchedule

DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

class DayHours(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['hours', 'all_day', 'none'] = 'none'
    start: str = ''
    end: str = ''

    @model_validator(mode='after')
    def valid(self):
        if self.mode != 'hours':
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
    start, end = minutes(day['start']), end_minutes(day['end'])
    return [(a,b) for a,b in [(0,start),(end,1440)] if a < b]

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
                result.append({'type':1, 'start_time':at(first,a).isoformat(),
                    'end_time':at(first,b).isoformat(), 'all_day':a==0 and b==1440,
                    'notes':'Weekly hours approved in the availability portal', 'recurrence':rule})
    return result


def week_totals(schedule):
    unavailable = sum(b-a for day in schedule['days'] for a,b in blocked(day))
    def label(value):
        hours, mins = divmod(value, 60)
        return f'{hours}h' + (f' {mins}m' if mins else '')
    return {'available':label(7*1440-unavailable), 'unavailable':label(unavailable)}
