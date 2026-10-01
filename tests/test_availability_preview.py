from datetime import date
from app.availability_preview import preview

def test_recurring_overlap_all_day_and_preferred():
    events=[dict(type=1,start_time='2026-10-01T05:00:00-04:00',end_time='2026-10-01T10:00:00-04:00',recurrence='FREQ=WEEKLY;BYDAY=TH'),
            dict(type=1,start_time='2026-10-01T09:00:00-04:00',end_time='2026-10-01T11:00:00-04:00'),
            dict(type=1,start_time='2026-10-02T00:00:00-04:00',end_time='2026-10-02T23:59:59-04:00',all_day=True),
            dict(type=2,start_time='2026-10-03T08:00:00-04:00',end_time='2026-10-03T12:00:00-04:00')]
    rows=preview(events,date(2026,10,1))
    assert rows[0]['hours']=='11:00 AM–11:00 PM'
    assert rows[1]['hours']=='No available hours'
    assert rows[2]['hours']=='5:00 AM–11:00 PM'
    assert preview(events,date(2026,10,8))[0]['hours']=='10:00 AM–11:00 PM'

def test_overnight_and_multiple_open_windows():
    events=[dict(type=1,start_time='2026-09-30T22:00:00-04:00',end_time='2026-10-01T07:00:00-04:00'),
            dict(type=1,start_time='2026-10-01T12:00:00-04:00',end_time='2026-10-01T14:00:00-04:00')]
    assert preview(events,date(2026,10,1))[0]['hours']=='7:00 AM–12:00 PM, 2:00 PM–11:00 PM'
