import base64,hashlib,hmac,json
from sqlalchemy import select,func
from app.config import settings
from app.models import User,WebhookBatch,Scope,AdminAudit
from app.webhooks import process_webhooks


def post(client,secret='hook-secret',uid=45,account=None):
    body=json.dumps([{'uuid':'event-1','type':'users::created','userId':'999','data':{'userId':str(uid)}}]).encode()
    return client.post('/webhooks/wiw',content=body,headers={'X-Signed-Hmac-256':base64.b64encode(hmac.new(secret.encode(),body,hashlib.sha256).digest()).decode(),'X-Account-Id':str(settings().wiw_account_id if account is None else account)})


def test_receiver_signature_workplace_dedup(client,db,monkeypatch):
    monkeypatch.setattr(settings(),'wiw_webhook_secret','hook-secret')
    assert post(client,secret='wrong').status_code==403
    assert post(client,account=-3).status_code==403
    assert post(client).status_code==200
    assert post(client).status_code==200
    assert db.scalar(select(func.count()).select_from(WebhookBatch))==1
    assert db.scalar(select(WebhookBatch)).user_ids==[45]


def test_worker_fetches_current_record_preserves_roles(client,db,monkeypatch):
    monkeypatch.setattr(settings(),'wiw_webhook_secret','hook-secret')
    monkeypatch.setattr(settings(),'wiw_mode','live')
    db.get(User,2).role='admin';db.commit()
    assert post(client,uid=1).status_code==200
    from sqlalchemy.orm import sessionmaker
    factory=sessionmaker(bind=db.get_bind(),expire_on_commit=False)
    class API:
        def call(self,method,path):
            assert method=='GET'
            if path=='/2/locations': return {'locations':[dict(id=5,account_id=settings().wiw_account_id,name='New Schedule',is_deleted=False)]}
            assert path=='/2/users/1'
            return {'user':dict(id=1,account_id=settings().wiw_account_id,activated=True,is_deleted=False,first_name='New',last_name='Name',locations=[5])}
    assert process_webhooks(factory,API)==1
    db.expire_all()
    assert db.get(User,1).name=='New Name'
    assert db.get(User,1).location=='New Schedule'
    assert db.get(User,2).role=='admin'
    assert db.scalar(select(func.count()).select_from(Scope))==2
    assert db.scalar(select(WebhookBatch)).status=='done'


def test_worker_bad_account_rolls_back(client,db,monkeypatch):
    monkeypatch.setattr(settings(),'wiw_webhook_secret','hook-secret')
    monkeypatch.setattr(settings(),'wiw_mode','live')
    db.get(User,2).role='admin';db.commit();post(client)
    from sqlalchemy.orm import sessionmaker
    factory=sessionmaker(bind=db.get_bind(),expire_on_commit=False)
    class API:
        def call(self,*args): return {'locations':[dict(id=1,account_id=-1,name='Bad',is_deleted=False)]}
    process_webhooks(factory,API)
    db.expire_all()
    assert db.scalar(select(WebhookBatch)).status=='retry'
    assert db.scalar(select(User).where(User.wiw_user_id==45)) is None


def test_rejected_webhook_logs_structure_without_personal_values(client,monkeypatch,caplog):
    monkeypatch.setattr(settings(),'wiw_webhook_secret','hook-secret')
    payload={'events':[{'type':'users::updated','data':{'userId':'private-employee-value','fields':{'email':{'new':'private@example.com'}}}}]}
    body=json.dumps(payload).encode()
    signature=base64.b64encode(hmac.new(b'hook-secret',body,hashlib.sha256).digest()).decode()
    response=client.post('/webhooks/wiw',content=body,headers={'X-Signed-Hmac-256':signature,'X-Account-Id':str(settings().wiw_account_id)})
    assert response.status_code==400
    assert 'WIW webhook rejected' in caplog.text
    assert 'events' in caplog.text and 'array' in caplog.text
    assert 'private' not in caplog.text and signature not in caplog.text
    assert 'hook-secret' not in caplog.text
