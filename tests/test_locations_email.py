from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException
import pytest
from app.models import User, Scope, Change, EmailOutbox
from app.config import settings
from app.workflow import decide
from app.notifications import enqueue_notifications
from app.mailer import process_batch
from test_portal import submit, sign_in, Fake


def enable(monkeypatch):
    monkeypatch.setattr(settings(),'email_enabled',True)
    monkeypatch.setattr(settings(),'public_base_url','https://portal.example.com')


def test_secondary_manager_can_see_and_decide_once(client,db):
    db.get(User,1).secondary_location='South';db.commit()
    submit(client)
    sign_in(client,'other@test.local')
    assert client.get('/requests/1').status_code==200
    assert 'employee' in client.get('/').text
    decide(db,db.get(User,3),1,'approve','',Fake())
    with pytest.raises(HTTPException): decide(db,db.get(User,2),1,'approve','',Fake())


def test_five_admin_cap_and_self_demotion(client,db):
    db.get(User,2).role='admin'
    for i in range(4): db.add(User(email=f'a{i}@example.com',name='Admin',role='admin',wiw_user_id=100+i,location='North',password_hash='!'))
    db.commit()
    csrf=sign_in(client,'manager@test.local')
    data={'csrf':csrf,'role':'admin','location':'North','scopes':'North'}
    assert client.post('/admin/users/1',data=data).status_code==409
    data['role']='employee'
    assert client.post('/admin/users/2',data=data).status_code==422
    data.update(role='manager',secondary_location='North')
    assert client.post('/admin/users/1',data=data).status_code==422


def test_notifications_both_locations_dedup_and_decision(client,db,monkeypatch):
    enable(monkeypatch)
    for i in (1,2,3): db.get(User,i).notification_email=f'user{i}@example.com'
    db.get(User,1).secondary_location='South'
    db.add(Scope(manager_id=2,location='South'));db.commit()
    submit(client)
    rows=list(db.scalars(select(EmailOutbox)))
    assert len(rows)==2 and {r.recipient_id for r in rows}=={2,3}
    assert all('employee' in r.subject and 'https://portal.example.com/requests/1' in r.body for r in rows)
    enqueue_notifications(db,db.get(Change,1),'submitted');db.commit()
    assert len(list(db.scalars(select(EmailOutbox))))==2
    decide(db,db.get(User,2),1,'reject','Please speak with me',Fake())
    item=db.scalar(select(EmailOutbox).where(EmailOutbox.event=='rejected'))
    assert item.recipient_id==1 and 'Please speak with me' in item.body


def test_outbox_worker_retries_and_rechecks_access(client,db,monkeypatch):
    enable(monkeypatch)
    db.get(User,2).notification_email='manager@example.com';db.commit()
    submit(client)
    factory=sessionmaker(bind=db.get_bind(),expire_on_commit=False)
    def fail(item): raise RuntimeError('secret-password')
    process_batch(factory,fail)
    db.expire_all()
    item=db.scalar(select(EmailOutbox))
    assert item.status=='retry' and item.last_error=='RuntimeError'
    from app.models import now
    item.next_attempt=now();db.get(User,2).role='employee';db.commit()
    calls=[]
    process_batch(factory,lambda item:calls.append(item.id))
    db.expire_all()
    assert db.get(EmailOutbox,item.id).status=='cancelled' and not calls


def test_disabled_email_and_approved_test_message(client,db,monkeypatch):
    submit(client)
    assert not list(db.scalars(select(EmailOutbox)))
    enable(monkeypatch)
    db.get(User,1).notification_email='employee@example.com';db.commit()
    decide(db,db.get(User,2),1,'approve','Looks good',Fake())
    item=db.scalar(select(EmailOutbox))
    assert item.event=='approved' and 'test approval' in item.body
    factory=sessionmaker(bind=db.get_bind(),expire_on_commit=False)
    sent=[]
    assert process_batch(factory,lambda row:sent.append(row.recipient))==1
    assert sent==['employee@example.com']
