import re
from datetime import timedelta
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from app.models import Change, Audit, User, LoginSession, now
from app.workflow import decide
from app.wiw import WIWError

def token(response):
    return re.search(r'name="csrf" value="([^"]+)"', response.text)[1]

def sign_in(client, email='employee@test.local'):
    csrf = token(client.get('/login'))
    response = client.post('/login', data={'csrf': csrf,'email':email,'password':'test-password-123'})
    assert response.status_code == 200
    return token(response)

def submit(client):
    csrf = sign_in(client)
    date = (now()+timedelta(days=30)).strftime('%Y-%m-%d')
    data = {'csrf':csrf,'action':'weekly','effective_date':date,'employee_note':'New term'}
    for i in range(7): data[f'day_{i}_mode']='all_day'
    data.update(day_0_mode='hours', day_0_start='09:00', day_0_end='17:00')
    response = client.post('/requests', data=data)
    assert response.status_code == 200, response.text
    return response

class Fake:
    def __init__(self, state=None, fail=False):
        self.state = state or {'availabilityevents': []}
        self.writes = 0
        self.fail = fail
    def read(self, *args): return self.state
    def weekly_operation(self, change, operation):
        assert change.status == 'applying' and change.manager_id == 2
        self.writes += 1
        if self.fail: raise WIWError('timeout')
        return {'availabilityevent': {'id':100+self.writes, 'user_id':change.wiw_user_id, 'account_id':10, **operation['payload']}}


def test_employee_submission_and_dry_run(client, db):
    submit(client)
    c = db.scalar(select(Change))
    assert c.status == 'pending'
    assert c.proposed['days'][0] == {'mode':'hours','start':'09:00','end':'17:00','ranges':[]}
    provider = Fake()
    decide(db, db.get(User,2), c.id, 'approve','Fine', provider)
    assert c.status == 'approved_dry_run' and provider.writes == 0
    assert len(db.scalars(select(Audit)).all()) == 2
    with pytest.raises(HTTPException) as e: decide(db,db.get(User,2),c.id,'approve','',provider)
    assert e.value.status_code == 409


def test_scope_and_employee_cannot_decide(client, db):
    submit(client)
    for uid in [1,3]:
        with pytest.raises(HTTPException): decide(db,db.get(User,uid),1,'approve','',Fake())
    sign_in(client, 'other@test.local')
    assert client.get('/requests/1').status_code == 404


def test_rejection_requires_reason(client, db):
    submit(client)
    with pytest.raises(HTTPException): decide(db,db.get(User,2),1,'reject','',Fake())
    provider = Fake()
    c = decide(db,db.get(User,2),1,'reject','Coverage needed',provider)
    assert c.status == 'rejected' and provider.writes == 0


def test_conflict_never_writes(client, db):
    submit(client)
    provider = Fake({'availabilityevents':[{'id':7,'notes':'Changed outside portal'}]})
    c = decide(db,db.get(User,2),1,'approve','',provider)
    assert c.status == 'conflict' and provider.writes == 0


def test_live_write_and_uncertain_delivery(client, db, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings(), 'dry_run', False)
    submit(client)
    provider = Fake(fail=True)
    c = decide(db,db.get(User,2),1,'approve','Approved',provider)
    assert c.status == 'needs_reconciliation' and provider.writes == 1
    approval = db.scalar(select(Audit).where(Audit.event == 'approved'))
    assert approval.details['pre_write_state'] == {'availabilityevents':[]}
    with pytest.raises(HTTPException): decide(db,db.get(User,2),1,'approve','',provider)
    assert provider.writes == 1


def test_successful_live_delivery(client, db, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings(),'dry_run',False)
    submit(client)
    provider = Fake()
    c = decide(db,db.get(User,2),1,'approve','Approved',provider)
    assert c.status == 'applied' and provider.writes == 2


def test_csrf_and_session_revocation(client, db):
    assert client.post('/login',data={'email':'employee@test.local'}).status_code == 403
    csrf = sign_in(client)
    assert client.post('/requests',data={'action':'create'}).status_code == 403
    assert client.post('/logout',data={'csrf':csrf}).status_code == 200
    assert db.scalar(select(LoginSession)) is None
    assert client.get('/').url.path == '/login'


def test_session_expiry_and_inactive(client, db):
    sign_in(client)
    session = db.scalar(select(LoginSession)); session.expires = now()-timedelta(seconds=1); db.commit()
    assert client.get('/').url.path == '/login'
    sign_in(client)
    db.get(User,1).active = False; db.commit()
    assert client.get('/').url.path == '/login'


def test_rate_limit(client):
    csrf = token(client.get('/login'))
    for _ in range(10):
        client.post('/login',data={'csrf':csrf,'email':'x@test.local','password':'bad'})
    assert client.post('/login',data={'csrf':csrf,'email':'x@test.local','password':'bad'}).status_code == 429


def test_headers_and_host(client):
    r = client.get('/login')
    assert "script-src 'self'" in r.headers['content-security-policy']
    assert 'httponly' in r.headers['set-cookie'].lower()
    assert client.get('/login',headers={'host':'evil.example'}).status_code == 400


def test_manager_web_decision(client, db):
    submit(client)
    csrf = sign_in(client,'manager@test.local')
    response = client.post('/requests/1/decision',data={'csrf':csrf,'decision':'approve','note':'OK'})
    assert response.status_code == 200 and 'Approved · test only' in response.text
    assert db.get(Change,1).status == 'approved_dry_run'


def test_invalid_dates_and_escaping(client):
    csrf = sign_in(client)
    assert client.post('/requests',data={'csrf':csrf,'action':'create','type':'1','start_time':'2020-01-01T09:00','end_time':'2020-01-01T10:00'}).status_code == 422
    assert client.get('/availability').status_code == 200


def test_mapping_changed_blocks_approval(client, db):
    submit(client)
    db.get(User,1).location='South'; db.commit()
    with pytest.raises(HTTPException) as e: decide(db,db.get(User,2),1,'approve','',Fake())
    assert e.value.status_code==409


def test_uncertain_previous_request_blocks_next(client, db):
    submit(client)
    db.get(Change,1).status='needs_reconciliation'; db.commit()
    submit(client)
    with pytest.raises(HTTPException) as e: decide(db,db.get(User,2),2,'approve','',Fake())
    assert e.value.status_code==409


def test_read_error_leaves_request_pending(client, db):
    submit(client)
    class Unavailable(Fake):
        def read(self,*args): raise WIWError('Unavailable')
    with pytest.raises(WIWError): decide(db,db.get(User,2),1,'approve','',Unavailable())
    assert db.get(Change,1).status=='pending'


def test_audit_html_escapes_notes(client, db):
    submit(client)
    db.get(Change,1).employee_note='<script>alert(1)</script>'; db.commit()
    response=client.get('/requests/1')
    assert '<script>alert(1)</script>' not in response.text
    assert '&lt;script&gt;' in response.text


def test_postgres_concurrent_decision(client, db, monkeypatch):
    if db.bind.dialect.name != 'postgresql': pytest.skip('Requires TEST_DATABASE_URL with PostgreSQL')
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Lock
    from sqlalchemy.orm import Session
    from app.config import settings
    monkeypatch.setattr(settings(),'dry_run',False)
    submit(client)
    barrier, mutex = Barrier(2), Lock()
    class Counted(Fake):
        def weekly_operation(self,change,operation):
            with mutex: self.writes += 1
            return {'availabilityevent': {'id':999+self.writes,'user_id':1,'account_id':10,**operation['payload']}}
    provider=Counted()
    def worker():
        with Session(db.bind,expire_on_commit=False) as session:
            actor=session.get(User,2)
            barrier.wait(timeout=10)
            try: return decide(session,actor,1,'approve','',provider).status
            except HTTPException as exc: return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:worker(),range(2)))
    assert provider.writes==2
    assert sorted(map(str,results))==['409','applied']
