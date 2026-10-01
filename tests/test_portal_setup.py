import re
from datetime import timedelta
from sqlalchemy import select
from app.models import User, PortalSetup, LoginSession, now
from app.security import digest, verify
from test_portal import sign_in, token


def issue(client,db):
    db.get(User,2).role='admin'; db.commit()
    csrf=sign_in(client,'manager@test.local')
    response=client.post('/admin/users/1/portal-login',data={'csrf':csrf,'email':'employee@example.com'})
    assert response.status_code==200
    return re.findall(r'<input readonly value="([^"]+)"',response.text)[1]


def redeem(client,code):
    csrf=token(client.get('/setup'))
    return client.post('/setup',data={'csrf':csrf,'code':code,'password':'new-secret-password','confirm':'new-secret-password'})


def test_setup_existing_account_once_and_session_revocation(client,db):
    sign_in(client)
    code=issue(client,db)
    assert db.get(PortalSetup,1).digest==digest(code)
    assert db.get(User,1).email=='employee@test.local'
    assert redeem(client,code).status_code==200
    db.expire_all()
    person=db.get(User,1)
    assert person.email=='employee@example.com' and person.wiw_user_id==1
    assert person.role=='employee' and person.location=='North'
    assert verify('new-secret-password',person.password_hash)
    assert db.scalar(select(LoginSession).where(LoginSession.user_id==1)) is None
    assert redeem(client,code).status_code==400


def test_setup_permissions_and_csrf(client,db):
    csrf=sign_in(client)
    assert client.post('/admin/users/1/portal-login',data={'csrf':csrf,'email':'e@example.com'}).status_code==403
    db.get(User,2).role='admin';db.commit()
    sign_in(client,'manager@test.local')
    assert client.post('/admin/users/1/portal-login',data={'email':'e@example.com'}).status_code==403
    assert client.post('/setup',data={}).status_code==403


def test_reissue_expired_disabled_and_credential_changed(client,db):
    old=issue(client,db); code=issue(client,db)
    assert redeem(client,old).status_code==400
    db.get(PortalSetup,1).expires=now()-timedelta(seconds=1); db.commit()
    assert redeem(client,code).status_code==400
    code=issue(client,db)
    db.get(User,1).active=False;db.commit()
    assert redeem(client,code).status_code==400
    db.get(User,1).active=True;db.commit()
    code=issue(client,db)
    db.get(User,1).password_hash='changed';db.commit()
    assert redeem(client,code).status_code==400
