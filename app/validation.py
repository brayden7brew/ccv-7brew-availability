from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal
from .config import settings

class EventInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start_time: datetime
    end_time: datetime
    type: Literal[1, 2]
    all_day: bool = False
    notes: str = Field(default='', max_length=160)
    weeks: int = Field(default=1, ge=1, le=52)

    @model_validator(mode='after')
    def valid(self):
        zone = ZoneInfo(settings().business_timezone)
        for name in ('start_time', 'end_time'):
            value = getattr(self, name)
            if value.tzinfo is None:
                aware = value.replace(tzinfo=zone)
                # Reject ambiguous/nonexistent wall times instead of silently shifting them.
                if aware.utcoffset() != aware.replace(fold=1).utcoffset():
                    raise ValueError('This time is ambiguous or nonexistent during a daylight-saving transition.')
                setattr(self, name, aware)
        today = datetime.now(zone).date()
        if self.start_time.astimezone(zone).date() < today + timedelta(days=max(1, settings().minimum_notice_days)):
            raise ValueError('Start date does not meet the minimum notice period.')
        if not timedelta(0) < self.end_time - self.start_time <= timedelta(days=1):
            raise ValueError('Each event must be longer than zero and at most 24 hours.')
        if self.all_day and (self.start_time.hour or self.start_time.minute or self.end_time.hour or self.end_time.minute or (self.end_time.date()-self.start_time.date()).days != 1):
            raise ValueError('All-day events must run midnight to next midnight.')
        return self

    def payload(self):
        data = self.model_dump(mode='json', exclude={'weeks'})
        if self.weeks > 1:
            data['recurrence'] = f'FREQ=WEEKLY;COUNT={self.weeks}'
        return data
