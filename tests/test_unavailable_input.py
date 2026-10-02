from datetime import timedelta
from dateutil.parser import parse
from app.weekly import WeeklyInput, blocked, event_plan, local_today, DAYS
from app.availability_rules import counted_minutes
from app.models import Change, WeeklySchedule, User
from test_portal import sign_in, submit, Fake
from app.workflow import decide


def test_unavailable_interval_counts_complement_and_keeps_weekday():
    days = [dict(mode='none', start='', end='') for _ in DAYS]
    days[1] = dict(mode='unavailable', start='10:00', end='18:00')
    data = WeeklyInput(effective_date=local_today()+timedelta(days=30), days=days)
    stored = data.model_dump(mode='json')
    assert blocked(stored['days'][1]) == [(600, 1080)]
    assert counted_minutes(stored['days']) == 600  # 5 before + 5 after
    events = event_plan([stored])
    monday = [e for e in events if e['recurrence'].endswith('BYDAY=MO')]
    assert len(monday) == 1
    assert parse(monday[0]['start_time']).weekday() == 0
    assert parse(monday[0]['start_time']).hour == 10
    assert parse(monday[0]['end_time']).hour == 18
    days[1].update(start='05:00', end='23:00')
    assert counted_minutes(days) == 0
    days[1].update(start='12:00', end='13:00')
    assert counted_minutes(days) == 600


def test_form_defaults_unavailable_even_with_existing_schedule(client, db):
    submit(client)
    decide(db, db.get(User, 2), 1, 'approve', '', Fake())
    assert db.query(WeeklySchedule).count() == 1
    sign_in(client)
    html = client.get('/requests/new').text
    assert html.count('selected>I’m unavailable all day') == 7
    assert 'Welcome!' not in html
    assert 'Please note you must be able to work a minimum of 15 hours a week.' in html
    assert 'time-off request in When I Work' in html
    assert '/static/install.js' not in html
    assert 'install-panel' not in html


def test_unavailable_submission_and_minimum_enforced(client, db):
    token = sign_in(client)
    values = dict(csrf=token, action='weekly', effective_date=(local_today()+timedelta(days=30)).isoformat())
    values.update({f'day_{i}_mode':'none' for i in range(7)})
    values.update(day_1_mode='unavailable', day_1_start='05:00', day_1_end='18:00')
    bad = client.post('/requests', data=values)
    assert bad.status_code == 422 and 'counts as 5 hours' in bad.text
    values.update(day_2_mode='all_day')
    response = client.post('/requests', data=values, follow_redirects=False)
    assert response.status_code == 303
    change = db.query(Change).one()
    assert change.proposed['days'][1]['mode'] == 'unavailable'
    assert counted_minutes(change.proposed['days']) == 900


def test_home_calendar_is_monday_first_and_below_change_button(client, monkeypatch):
    from app.wiw import WIW
    monkeypatch.setattr(WIW, 'read', lambda *args: {'availabilityevents': []})
    sign_in(client)
    html = client.get('/').text
    assert html.index('home-availability-action') < html.index('My current availability') < html.index('calendar-week')
    assert html.index('>Mon</span>') < html.index('>Sun</span>')
    assert 'install-panel' not in html
