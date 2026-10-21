import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from app.config import settings
from app.models import Audit, Change, EmailOutbox, User
from app.webhooks import sync_employee
from app.approval_worker import process_approval
from app.mailer import process_batch
from test_portal import submit, sign_in, Fake


@pytest.mark.parametrize('status', ['pending','queued','preparing','applying','needs_reconciliation','applied','rejected'])
def test_archive_hides_requests_silently_preserves_write_history(client,db,monkeypatch,status):
    submit(client)
    change=db.get(Change,1)
    change.status=status
    for recipient in (1,2):
        db.add(EmailOutbox(change_id=1,recipient_id=recipient,event='submitted',
            recipient='person@example.com',subject='Request',body='Request',status='queued'))
    db.commit()
    sign_in(client,'manager@test.local')
    assert '/requests/1' in client.get('/requests').text
    class API:
        def call(self,method,path):
            assert method=='GET'
            return {'user':dict(id=1,account_id=settings().wiw_account_id,
                activated=True,is_deleted=True,first_name='Employee',locations=[5])}
    for _ in range(2):
        sync_employee(db,db.get(User,2),API(),1,{5:'North'})
        db.commit()
    assert not db.get(User,1).active
    assert change.status==('cancelled_archived' if status in ('pending','queued') else status)
    assert '/requests/1' not in client.get('/requests').text
    assert len(db.scalars(select(Audit).where(Audit.event=='employee_archived')).all())==1
    assert len(db.scalars(select(EmailOutbox)).all())==2
    assert all(e.status=='cancelled' for e in db.scalars(select(EmailOutbox)))
    # Historical request is retained, and no cancelled job can reach WIW.
    db.get(User,2).role='admin';db.commit()
    assert client.get('/requests/1').status_code==200
    factory=sessionmaker(db.bind,expire_on_commit=False)
    assert process_approval(factory,Fake)==0
    monkeypatch.setattr(settings(),'email_enabled',True)
    def fail(item): raise AssertionError('No archive notifications may be sent')
    assert process_batch(factory,fail)==0


def test_active_wiw_update_leaves_request_visible(client,db):
    submit(client)
    class API:
        def call(self,*args):
            return {'user':dict(id=1,account_id=settings().wiw_account_id,
                activated=True,is_deleted=False,first_name='Employee',locations=[5])}
    sync_employee(db,db.get(User,2),API(),1,{5:'North'});db.commit()
    assert db.get(Change,1).status=='pending'
    assert not db.scalar(select(Audit.id).where(Audit.event=='employee_archived'))
    sign_in(client,'manager@test.local')
    assert '/requests/1' in client.get('/requests').text
