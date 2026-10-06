from datetime import timedelta
from app.models import Change, User
from app.weekly import local_today
from app.wiw import WIWError
from test_portal import sign_in


def form(client):
    result={'csrf':sign_in(client),'action':'weekly',
            'effective_date':(local_today()+timedelta(days=30)).isoformat(),
            'employee_note':'Keep my note <script>alert(1)</script>'}
    result.update({f'day_{i}_mode':'none' for i in range(7)})
    result.update(day_0_mode='hours',day_0_start='05:00',day_0_end='10:00')
    return result


def test_insufficient_hours_keep_form_values_and_do_not_read_wiw(client,db,monkeypatch):
    def unexpected(): raise AssertionError('Invalid form must not call WIW')
    values=form(client)
    monkeypatch.setattr('app.main.WIW',unexpected)
    response=client.post('/requests',data=values)
    assert response.status_code==422
    assert 'id="weekly-form"' in response.text and 'We couldn’t complete that' not in response.text
    assert f'value="{values["effective_date"]}"' in response.text
    assert 'selected>5:00 AM' in response.text and 'selected>10:00 AM' in response.text
    assert 'Keep my note &lt;script&gt;' in response.text
    assert '<script>alert(1)</script>' not in response.text
    assert 'counts as 5 hours' in response.text
    assert db.query(Change).count()==0


def test_changed_notice_rules_are_displayed_with_preserved_entries(client,db):
    values=form(client)
    person=db.get(User,1); person.first_request_notice_exception=False; person.notice_days=40; db.commit()
    response=client.post('/requests',data=values)
    assert response.status_code==422 and '40 days' in response.text
    assert 'id="weekly-form"' in response.text and 'Keep my note' in response.text
    assert db.query(Change).count()==0


def test_wiw_failure_preserves_valid_form_without_submission(client,db,monkeypatch):
    values=form(client); values.update(day_1_mode='all_day')
    class Unavailable:
        def read(self,*args): raise WIWError('timeout')
    monkeypatch.setattr('app.main.WIW',Unavailable)
    response=client.post('/requests',data=values)
    assert response.status_code==503 and 'Your request has not been submitted' in response.text
    assert 'id="weekly-form"' in response.text and 'Keep my note' in response.text
    assert db.query(Change).count()==0


def test_form_script_is_local_and_disabled_until_validated(client):
    sign_in(client)
    response=client.get('/requests/new')
    assert 'id="weekly-submit" disabled' in response.text
    assert '<script src="/static/weekly-form.js" defer>' in response.text
    assert "script-src 'self'" in response.headers['content-security-policy']
    assert 'unsafe-inline' not in response.headers['content-security-policy']
    assert "script-src 'self'" in client.get('/').headers['content-security-policy']


def test_wiw_diagnostics_report_codes_without_credentials_or_upstream_text(client,db,monkeypatch,caplog):
    import logging
    values=form(client);values.update(day_1_mode='all_day')
    class Denied:
        def read(self,*args):
            raise WIWError('upstream private token and employee details',reason='http_error',http_status=401,wiw_code=1000)
    monkeypatch.setattr('app.main.WIW',Denied)
    with caplog.at_level(logging.WARNING):
        response=client.post('/requests',data=values)
    assert response.status_code==503 and 'Ask your administrator to check the connection' in response.text
    assert 'http_status=401 wiw_code=1000' in caplog.text
    assert 'upstream private' not in response.text+caplog.text
    import re
    reference=re.search(r'Reference: ([a-f0-9]+)',response.text)[1]
    assert f'reference={reference}' in caplog.text
    assert db.query(Change).count()==0
