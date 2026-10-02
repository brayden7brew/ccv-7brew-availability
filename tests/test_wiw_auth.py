import json
import httpx
import pytest
from sqlalchemy import select
from app.config import settings
from app.models import User, LoginSession, Audit
from app.wiw_auth import WIWAuth, WIWAuthError
from test_portal import token

@pytest.fixture
def auth_config(monkeypatch):
    monkeypatch.setattr(settings(),'wiw_developer_key','private-developer-key')
    monkeypatch.setattr(settings(),'wiw_account_id',10)
    monkeypatch.setattr(settings(),'wiw_token','service-write-token')

def member(**changes):
    return {'id':1,'account_id':10,'login_id':90,'activated':True,'is_deleted':False,**changes}

def test_documented_login_and_membership_contract(auth_config):
    calls=[]
    def handle(request):
        calls.append(request)
        if len(calls)==1:
            assert str(request.url)=='https://api.login.wheniwork.com/login'
            assert request.headers['W-Key']=='private-developer-key'
            assert json.loads(request.content)=={'email':'real@wiw.test','password':'my-wiw-password'}
            assert 'Authorization' not in request.headers
            return httpx.Response(200,json={'person':{'id':'90','token':'personal-token'},'token':'personal-token'})
        assert str(request.url)=='https://api.wheniwork.com/2/login'
        assert request.headers['Authorization']=='Bearer personal-token'
        assert 'W-Key' not in request.headers and 'W-UserID' not in request.headers
        return httpx.Response(200,json={'users':[member(account_id=20,id=2),member()]})
    assert WIWAuth(httpx.MockTransport(handle)).authenticate('real@wiw.test','my-wiw-password')==1
    assert len(calls)==2
    assert settings().wiw_token=='service-write-token'

@pytest.mark.parametrize('record',[member(account_id=99),member(login_id=99),member(activated=False),member(is_deleted=True),member(activated=1),member(id='1')])
def test_inactive_wrong_workplace_or_identity_denied(auth_config,record):
    def handle(request):
        if request.method=='POST': return httpx.Response(200,json={'person':{'id':'90','token':'personal-token'}})
        return httpx.Response(200,json={'users':[record]})
    with pytest.raises(WIWAuthError): WIWAuth(httpx.MockTransport(handle)).authenticate('a','b')

@pytest.mark.parametrize('status,body',[(401,{'error':'secret-password'}),(403,{'error':'secret-token'}),(429,{}),(200,{'person':{'id':'90'}}),(200,{'token':'challenge-only'})])
def test_failed_or_incomplete_login_never_creates_session(auth_config,status,body):
    calls=[]
    def handle(request):
        calls.append(request)
        return httpx.Response(status,json=body)
    with pytest.raises(WIWAuthError) as exc: WIWAuth(httpx.MockTransport(handle)).authenticate('a','b')
    assert 'secret' not in str(exc.value)
    assert len(calls)==1

def test_no_following_login_redirects(auth_config):
    calls=[]
    def handle(request):
        calls.append(request)
        return httpx.Response(302,headers={'Location':'https://other.example/login'})
    with pytest.raises(WIWAuthError): WIWAuth(httpx.MockTransport(handle)).authenticate('a','b')
    assert len(calls)==1

def test_real_wiw_email_maps_by_id_and_preserves_permissions(client,db,monkeypatch,auth_config):
    monkeypatch.setattr(WIWAuth,'authenticate',lambda self,email,password:1)
    old_hash=db.get(User,1).password_hash
    csrf=token(client.get('/login/wiw'))
    response=client.post('/login/wiw',data={'csrf':csrf,'email':'actual-wiw@example.com','password':'do-not-store-this'})
    assert response.status_code==200 and 'Hi, employee.' in response.text
    assert db.get(User,1).role=='employee' and db.get(User,1).password_hash==old_hash
    assert db.get(User,1).email=='employee@test.local'
    assert db.scalar(select(LoginSession)).user_id==1
    assert db.scalar(select(Audit)) is None
    for secret in ['do-not-store-this','private-developer-key','service-write-token']:
        assert secret not in response.text and secret not in str(client.cookies)

def test_manager_permissions_preserved(client,db,monkeypatch,auth_config):
    monkeypatch.setattr(WIWAuth,'authenticate',lambda self,email,password:2)
    csrf=token(client.get('/login/wiw'))
    response=client.post('/login/wiw',data={'csrf':csrf,'email':'actual-manager@example.com','password':'test'})
    assert 'MANAGE REQUESTS' in response.text.upper()
    assert db.get(User,2).role=='manager'

@pytest.mark.parametrize('local_state',['disabled','unprovisioned'])
def test_local_access_required(client,db,monkeypatch,auth_config,local_state):
    monkeypatch.setattr(settings(), 'wiw_auto_enroll', False)
    if local_state=='disabled': db.get(User,1).active=False; db.commit()
    monkeypatch.setattr(WIWAuth,'authenticate',lambda self,email,password:1 if local_state=='disabled' else 999)
    csrf=token(client.get('/login/wiw'))
    response=client.post('/login/wiw',data={'csrf':csrf,'email':'actual@example.com','password':'test'})
    assert 'portal access has not been enabled' in response.text
    assert db.scalar(select(LoginSession)) is None
    assert len(db.scalars(select(User)).all())==3

def test_wiw_csrf_and_throttle(client,monkeypatch,auth_config):
    calls=[]
    def fail(self,email,password):
        calls.append(email)
        raise WIWAuthError('Unable to sign in.')
    monkeypatch.setattr(WIWAuth,'authenticate',fail)
    assert client.post('/login/wiw',data={'email':'a','password':'b'}).status_code==403
    assert calls==[]
    csrf=token(client.get('/login/wiw'))
    for _ in range(10): client.post('/login/wiw',data={'csrf':csrf,'email':'a@b.com','password':'bad'})
    assert client.post('/login/wiw',data={'csrf':csrf,'email':'a@b.com','password':'bad'}).status_code==429
    assert len(calls)==10

def test_unconfigured_login_never_calls_upstream(monkeypatch):
    monkeypatch.setattr(settings(),'wiw_developer_key','')
    with pytest.raises(WIWAuthError): WIWAuth().authenticate('a','b')


def test_automatic_employee_enrollment_and_disabled_account(client, db, monkeypatch, auth_config):
    monkeypatch.setattr(settings(), 'wiw_auto_enroll', True)
    monkeypatch.setattr(settings(), 'wiw_auto_enroll_location', 'Test location')
    monkeypatch.setattr(WIWAuth, 'authenticate', lambda self,email,password: 999)
    csrf = token(client.get('/login/wiw'))
    response = client.post('/login/wiw', data={'csrf':csrf, 'email':'manager@test.local', 'password':'private'})
    assert response.status_code == 200
    user = db.scalar(select(User).where(User.wiw_user_id == 999))
    assert user.role == 'employee' and user.location == ''
    assert user.password_hash == '!' and user.email != 'manager@test.local'
    assert db.get(User,2).role == 'manager'
    csrf = token(response)
    client.post('/logout', data={'csrf':csrf})
    user.active = False
    db.commit()
    csrf = token(client.get('/login/wiw'))
    response = client.post('/login/wiw', data={'csrf':csrf,'email':'x@example.com','password':'private'})
    assert 'portal access has not been enabled' in response.text
    assert len(list(db.scalars(select(User).where(User.wiw_user_id == 999)))) == 1
