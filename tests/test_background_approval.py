from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
import pytest
from app.models import Change, User, Audit
from app.config import settings
from app.workflow import decide
from app.approval_worker import process_approval
from app.wiw import WIWError
from test_portal import submit, sign_in, Fake


def test_web_approval_returns_to_list_without_wiw_calls(client, db, monkeypatch):
    submit(client)
    csrf=sign_in(client,'manager@test.local')
    def fail(*args, **kwargs): raise AssertionError('Web approval must not wait on WIW')
    monkeypatch.setattr('app.wiw.WIW.read',fail)
    response=client.post('/requests/1/decision',data={'csrf':csrf,'decision':'approve','note':'Approved'},follow_redirects=False)
    assert response.status_code==303 and response.headers['location']=='/requests'
    db.expire_all()
    assert db.get(Change,1).status=='queued'
    assert db.get(Change,1).manager_note=='Approved'
    page=client.get('/requests')
    assert 'Approved · waiting to sync' in page.text and 'request-status.js' in page.text
    again=client.post('/requests/1/decision',data={'csrf':csrf,'decision':'approve'},follow_redirects=False)
    assert again.status_code==303
    assert len(db.scalars(select(Audit).where(Audit.event=='approval_queued')).all())==1


@pytest.mark.parametrize('case', ['success','revoked','mapping','mode','read_error','write_error','crash'])
def test_durable_worker_claim_revalidates_and_never_replays(client, db, monkeypatch, case):
    monkeypatch.setattr(settings(),'dry_run',False)
    monkeypatch.setattr(settings(),'wiw_mode','live')
    provider=Fake()
    monkeypatch.setattr('app.main.WIW',lambda:provider)
    submit(client)
    decide(db,db.get(User,2),1,'approve','OK',enqueue=True)
    if case=='revoked': db.get(User,2).active=False
    if case=='mapping': db.get(User,1).location='South'
    if case=='mode': monkeypatch.setattr(settings(),'dry_run',True)
    db.commit()
    class Provider(Fake):
        def read(self,*args):
            if case=='read_error': raise WIWError('WIW unavailable')
            return super().read(*args)
        def weekly_operation(self,*args):
            if case=='write_error': raise WIWError('timeout')
            if case=='crash': raise RuntimeError('secret response must not appear in audit')
            return super().weekly_operation(*args)
    provider=Provider()
    factory=sessionmaker(db.bind,expire_on_commit=False)
    assert process_approval(factory,lambda:provider)==1
    db.expire_all()
    expected='applied' if case=='success' else 'needs_reconciliation' if case in ('write_error','crash') else 'approval_failed'
    assert db.get(Change,1).status==expected
    calls=provider.writes
    assert process_approval(factory,lambda:provider)==0
    assert provider.writes==calls
    assert 'secret response' not in str([e.details for e in db.scalars(select(Audit))])
    if case=='success': assert calls==2
    else: assert calls==0


def test_queued_work_blocks_second_approval_for_same_employee(client, db):
    from fastapi import HTTPException
    submit(client);submit(client)
    decide(db,db.get(User,2),1,'approve','',enqueue=True)
    with pytest.raises(HTTPException,match='earlier write'):
        decide(db,db.get(User,2),2,'approve','',enqueue=True)
    assert db.get(Change,2).status=='pending'


def test_queued_mode_survives_worker_restart(client, db):
    submit(client)
    decide(db,db.get(User,2),1,'approve','',enqueue=True)
    # A new DB session, independent of the request and its provider.
    factory=sessionmaker(db.bind,expire_on_commit=False)
    assert process_approval(factory,Fake)==1
    db.expire_all()
    assert db.get(Change,1).status=='approved_dry_run'


def test_interrupted_worker_is_not_replayed(client, db):
    submit(client)
    db.get(Change,1).status='preparing';db.commit()
    assert process_approval(sessionmaker(db.bind),Fake)==0


def test_two_postgres_workers_cannot_claim_same_approval(client,db,monkeypatch):
    if db.bind.dialect.name!='postgresql': pytest.skip('Requires PostgreSQL row locking')
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    monkeypatch.setattr(settings(),'dry_run',False)
    monkeypatch.setattr(settings(),'wiw_mode','live')
    provider=Fake()
    monkeypatch.setattr('app.main.WIW',lambda:provider)
    submit(client)
    decide(db,db.get(User,2),1,'approve','',enqueue=True)
    entered,release=Event(),Event()
    class Provider(Fake):
        def read(self,*args):
            entered.set()
            assert release.wait(10)
            return super().read(*args)
    provider=Provider()
    factory=sessionmaker(db.bind,expire_on_commit=False)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(process_approval,factory,lambda:provider)
        assert entered.wait(10)
        try: assert pool.submit(process_approval,factory,lambda:provider).result(5)==0
        finally: release.set()
        assert first.result(10)==1
    assert provider.writes==2
