from datetime import datetime
from app.presentation import clock_label, date_label, local_datetime
from app.models import User
from app.workflow import decide
from test_portal import submit, sign_in, Fake


def test_local_dates_and_clocks():
    assert clock_label('00:00') == '12:00 AM'
    assert clock_label('12:00') == '12:00 PM'
    assert clock_label('23:00') == '11:00 PM'
    assert date_label('2026-10-15') == 'Oct 15, 2026'
    assert local_datetime(datetime(2026, 1, 1, 2, 30)) == 'Dec 31, 2025 at 9:30 PM EST'
    assert local_datetime('2026-07-01T14:00:00+00:00') == 'Jul 1, 2026 at 10:00 AM EDT'
    assert local_datetime('unknown') == 'unknown'


def test_employee_sees_manager_note_without_raw_audit(client, db):
    submit(client)
    note='Please add Monday.\n<script>alert(1)</script>'
    decide(db, db.get(User,2), 1, 'reject', note, Fake())
    html=client.get('/requests/1').text
    assert 'Message from your manager' in html and 'Please add Monday.' in html
    assert '&lt;script&gt;' in html and '<script>' not in html
    assert 'Technical details' not in html and 'Actor #' not in html
    assert 'Manager declined' in html
    assert '68 hours count toward the weekly minimum' in html
    sign_in(client,'manager@test.local')
    assert 'Technical details' in client.get('/requests/1').text
