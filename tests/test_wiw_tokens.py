import base64
import json
from datetime import timedelta
import httpx
import pytest
from sqlalchemy.orm import sessionmaker
from app.config import settings
from app.models import WIWCredential, now
from app.wiw_tokens import service_token, refresh_due, cipher, connection_status


def jwt(issued, **claims):
    payload = base64.urlsafe_b64encode(json.dumps({'iat':issued.timestamp(), **claims}).encode()).decode().rstrip('=')
    return f'header.{payload}.signature'


@pytest.fixture
def renewal(db, monkeypatch):
    cfg=settings()
    monkeypatch.setattr(cfg,'wiw_mode','live')
    monkeypatch.setattr(cfg,'wiw_auto_refresh',True)
    monkeypatch.setattr(cfg,'wiw_token',jwt(now()-timedelta(days=5)))
    db.add(WIWCredential(id=1)); db.commit()
    return sessionmaker(db.bind,expire_on_commit=False)


def test_refresh_is_persisted_encrypted_and_reused_after_restart(db,renewal):
    fresh=jwt(now())
    calls=[]
    def respond(request):
        calls.append(request)
        assert str(request.url)=='https://api.login.wheniwork.com/refresh'
        assert request.method=='POST' and request.headers['Authorization']==f'Bearer {settings().wiw_token}'
        return httpx.Response(200,json={'token':fresh})
    assert service_token(factory=renewal,transport=httpx.MockTransport(respond))==fresh
    assert service_token(factory=renewal,transport=httpx.MockTransport(respond))==fresh
    assert len(calls)==1
    db.expire_all(); row=db.get(WIWCredential,1)
    assert fresh not in row.encrypted_token
    assert cipher().decrypt(row.encrypted_token.encode()).decode()==fresh
    assert connection_status(db)['last_success'] and not connection_status(db)['error']


@pytest.mark.parametrize('status,body',[(401,{'token':'secret'}),(429,{}),(500,{}),(200,{}),(302,{}),(200,{'token':'bad token'})])
def test_failure_keeps_token_and_backs_off_without_leaking(db,renewal,status,body):
    calls=[]
    def respond(request):
        calls.append(request)
        return httpx.Response(status,json=body)
    old=settings().wiw_token
    for _ in range(2): assert service_token(factory=renewal,transport=httpx.MockTransport(respond))==old
    assert len(calls)==1
    db.expire_all(); row=db.get(WIWCredential,1)
    assert row.error and 'secret' not in row.error and old not in row.error
    assert not row.last_success


def test_changed_environment_token_reseeds_but_unchanged_seed_keeps_rotation(db,renewal,monkeypatch):
    fresh=jwt(now())
    service_token(factory=renewal,transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'token':fresh})))
    replacement=jwt(now()+timedelta(seconds=1))
    monkeypatch.setattr(settings(),'wiw_token',replacement)
    assert service_token(factory=renewal)==replacement


def test_schedule_uses_early_expiry_and_missing_claims():
    instant=now()
    assert refresh_due(jwt(instant),instant)==instant+timedelta(days=4)
    assert refresh_due(jwt(instant,exp=(instant+timedelta(days=3)).timestamp()),instant)==instant+timedelta(days=1)
    assert refresh_due('opaque',instant)==instant


def test_timeout_and_decryption_failure_are_safe(db,renewal,monkeypatch):
    def fail(request): raise httpx.ReadTimeout('upstream secret')
    assert service_token(factory=renewal,transport=httpx.MockTransport(fail))==settings().wiw_token
    monkeypatch.setattr(settings(),'secret_key','different-secret')
    with pytest.raises(RuntimeError,match='administrator attention'):
        service_token(factory=renewal)
    db.expire_all()
    assert 'cannot be decrypted' in connection_status(db)['error']


def test_wiw_call_uses_rotated_token_and_does_not_retry_write(monkeypatch):
    from app.wiw import WIW,WIWError
    monkeypatch.setattr('app.wiw_tokens.service_token',lambda:'rotated-secret')
    calls=[]
    def respond(request):
        calls.append(request)
        assert request.headers['Authorization']=='Bearer rotated-secret'
        return httpx.Response(401,json={'message':'rotated-secret rejected'})
    with pytest.raises(WIWError) as err:
        WIW(transport=httpx.MockTransport(respond)).call('POST','/2/availabilityevents',payload={})
    assert 'rotated-secret' not in str(err.value)
    assert len(calls)==1


def test_postgres_instances_refresh_only_once(db,renewal):
    if db.bind.dialect.name != 'postgresql': pytest.skip('Requires PostgreSQL row locking')
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Lock
    barrier, mutex = Barrier(2), Lock()
    fresh=jwt(now()); calls=[]
    def respond(request):
        with mutex: calls.append(1)
        return httpx.Response(200,json={'token':fresh})
    def worker():
        barrier.wait(timeout=10)
        return service_token(factory=renewal,transport=httpx.MockTransport(respond))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:worker(),range(2)))
    assert results==[fresh,fresh] and len(calls)==1
