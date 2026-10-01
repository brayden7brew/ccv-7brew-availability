from datetime import timezone
import pytest
from sqlalchemy import select
from app.models import LoginSession, now
from app.config import settings
from app.wiw_auth import WIWAuth
from test_portal import token

@pytest.mark.parametrize('wiw', [False, True])
@pytest.mark.parametrize('remember', [False, True])
def test_device_lifetime_and_logout(client, db, monkeypatch, wiw, remember):
    monkeypatch.setattr(WIWAuth, 'authenticate', lambda *a: 1)
    csrf = token(client.get('/login'))
    data = dict(csrf=csrf, email='employee@test.local', password='test-password-123')
    if remember: data['remember']='on'
    response = client.post('/login/wiw' if wiw else '/login', data=data, follow_redirects=False)
    assert response.status_code == 303
    seconds = 60*86400 if remember else settings().session_hours*3600
    assert f'Max-Age={seconds}' in response.headers['set-cookie']
    assert 'httponly' in response.headers['set-cookie'].lower()
    session = db.scalar(select(LoginSession))
    remaining = (session.expires.replace(tzinfo=timezone.utc)-now()).total_seconds()
    assert seconds-10 < remaining <= seconds
    csrf = token(client.get('/'))
    client.post('/logout',data={'csrf':csrf})
    assert db.scalar(select(LoginSession)) is None

def test_remembered_session_expires_server_side(client, db):
    from datetime import timedelta
    csrf=token(client.get('/login'))
    client.post('/login',data=dict(csrf=csrf,email='employee@test.local',password='test-password-123',remember='on'))
    session=db.scalar(select(LoginSession))
    session.expires=now()-timedelta(seconds=1)
    db.commit()
    assert client.get('/',follow_redirects=False).status_code==303
