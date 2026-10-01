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


def test_calendar_navigation_and_unavailable_provider(client,monkeypatch):
    from test_portal import sign_in
    from app.wiw import WIW, WIWError
    sign_in(client)
    seen=[]
    def read(self, uid, first, last):
        seen.append((first,last))
        return {'availabilityevents':[]}
    monkeypatch.setattr(WIW,'read',read)
    response=client.get('/availability')
    assert response.status_code==200
    assert 'calendar-week' in response.text and 'Previous week' in response.text
    assert response.text.count('class="calendar-day ')==7
    assert '5:00 AM–11:00 PM' in response.text
    assert client.get('/availability?week=invalid').status_code==422
    assert client.get('/availability?week=1900-01-01').status_code==422
    def failed(*args): raise WIWError('Unavailable')
    monkeypatch.setattr(WIW,'read',failed)
    response=client.get('/availability')
    assert response.status_code==200
    assert 'couldn’t load your hours' in response.text
    assert 'class="calendar-week"' not in response.text
