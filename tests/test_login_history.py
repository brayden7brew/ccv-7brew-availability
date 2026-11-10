from datetime import timedelta
from sqlalchemy import select
from app.models import LoginEvent, User, PortalSetup, now
from app.login_history import setup_status
from app.security import digest
from app.wiw_auth import WIWAuthError
from test_portal import sign_in, token


def test_failures_success_and_admin_only_history(client, db):
    csrf=token(client.get('/login'))
    client.post('/login',data={'csrf':csrf,'email':'employee@test.local','password':'do-not-store-me'})
    event=db.scalar(select(LoginEvent))
    assert event.outcome=='credentials' and event.method=='portal'
    assert 'do-not-store-me' not in str(event.__dict__)
    sign_in(client)
    assert client.get('/admin/login-history').status_code==403
    sign_in(client,'manager@test.local')
    assert client.get('/admin/login-history').status_code==403
    db.get(User,2).role='admin';db.commit()
    response=client.get('/admin/login-history?email=employee@test.local')
    assert response.status_code==200
    assert 'Email or password not accepted' in response.text
    assert 'Signed in successfully' in response.text
    assert 'do-not-store-me' not in response.text
    assert client.get('/admin/users/1').status_code==200
    assert 'Portal password is set' in client.get('/admin/users/1').text


def test_wiw_safe_reason_and_disabled_account(client,db,monkeypatch):
    from app.wiw_auth import WIWAuth
    def fail(*args): raise WIWAuthError('secret upstream payload','unavailable')
    monkeypatch.setattr(WIWAuth,'authenticate',fail)
    csrf=token(client.get('/login'))
    client.post('/login/wiw',data={'csrf':csrf,'email':'employee@test.local','password':'secret-pass'})
    event=db.scalar(select(LoginEvent))
    assert event.outcome=='unavailable'
    assert 'secret' not in str(event.__dict__)
    db.get(User,1).active=False;db.commit()
    client.post('/login',data={'csrf':csrf,'email':'employee@test.local','password':'test-password-123'})
    assert db.scalar(select(LoginEvent).order_by(LoginEvent.id.desc())).outcome=='disabled'


def test_rate_limit_and_retention(client,db):
    db.add(LoginEvent(email='old@test.local',method='portal',outcome='success',created=now()-timedelta(days=31)))
    db.commit()
    csrf=token(client.get('/login'))
    for _ in range(11):
        response=client.post('/login',data={'csrf':csrf,'email':'unknown@test.local','password':'wrong'})
    assert response.status_code==429
    assert db.scalar(select(LoginEvent).order_by(LoginEvent.id.desc())).outcome=='limited'
    assert not db.scalar(select(LoginEvent).where(LoginEvent.email=='old@test.local'))


def test_setup_status_never_reveals_code(client,db):
    person=db.get(User,1)
    setup=PortalSetup(user_id=1,digest=digest('private-code'),email=person.email,
        expires=now()+timedelta(hours=24),credential_version=digest(person.email+'\n'+person.password_hash))
    db.add(setup);db.commit()
    assert 'pending' in setup_status(db,person)
    setup.expires=now()-timedelta(seconds=1)
    assert 'expired' in setup_status(db,person)
    setup.credential_version='invalid'
    assert 'invalidated' in setup_status(db,person)


def test_wiw_server_failure_has_safe_unavailable_reason(monkeypatch):
    import httpx
    import pytest
    from app.config import settings
    from app.wiw_auth import WIWAuth
    monkeypatch.setattr(settings(),'wiw_developer_key','test-key')
    monkeypatch.setattr(settings(),'wiw_account_id',10)
    api=WIWAuth(httpx.MockTransport(lambda request: httpx.Response(503,text='private provider body')))
    with pytest.raises(WIWAuthError) as exc:
        api.authenticate('person@test.local','private-password')
    assert exc.value.reason=='unavailable'
    assert 'private' not in str(exc.value)


def test_directory_portal_usage_filters_only_successful_recent_signins(client,db):
    db.get(User,2).role='admin'
    db.get(User,3).password_hash='!'
    db.add_all([
        LoginEvent(user_id=1,email='employee@test.local',method='portal',outcome='success'),
        LoginEvent(user_id=3,email='other@test.local',method='portal',outcome='credentials'),
        LoginEvent(user_id=3,email='other@test.local',method='portal',outcome='success',created=now()-timedelta(days=31)),
        LoginEvent(user_id=3,email='other@test.local',method='wiw',outcome='success'),
    ])
    db.commit()
    sign_in(client,'manager@test.local')
    page=client.get('/admin?login_method=portal_used')
    assert page.status_code==200
    assert 'Used admin-provided portal login' in page.text
    assert '/admin/users/1?' in page.text and '/admin/users/2?' in page.text
    assert '/admin/users/3?' not in page.text
    assert 'login_method=portal_used' in page.text
    page=client.get('/admin?login_method=portal_enabled&role=employee')
    assert '/admin/users/1?' in page.text
    assert '/admin/users/2?' not in page.text and '/admin/users/3?' not in page.text
    db.get(User,1).active=False;db.commit()
    assert '/admin/users/1?' not in client.get('/admin?login_method=portal_used').text
    assert '/admin/users/1?' in client.get('/admin?login_method=portal_used&show_disabled=1').text
