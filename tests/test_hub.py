import pytest
from app.models import User, AdminAudit
from sqlalchemy import select
from test_portal import sign_in
from app import hub


def test_home_only_shows_granted_tools(client, db):
    sign_in(client)
    response = client.get('/')
    assert 'My Availability' in response.text
    assert 'Ops Dashboard' not in response.text
    assert client.get('/ops').status_code == 403
    assert client.get('/ops/api/dashboard').status_code == 403
    user = db.get(User, 1)
    user.ops_access = True
    user.ops_locations = ['ashland']
    db.commit()
    assert 'Ops Dashboard' in client.get('/').text
    assert client.get('/ops').status_code == 200
    assert client.get('/ops/stands/ashland').status_code == 200
    assert client.get('/ops/stands/hull-street').status_code == 403
    assert client.get('/ops/api/stands/hull-street').status_code == 403
    user.ops_access = False
    db.commit()
    assert client.get('/ops/api/dashboard').status_code == 403


def test_ops_filters_stands_and_requires_session(client, db, monkeypatch):
    assert client.get('/ops/api/dashboard', follow_redirects=False).status_code == 401
    sign_in(client)
    user = db.get(User, 1)
    user.ops_access = True
    user.ops_locations = ['ashland']
    db.commit()
    calls=[]
    monkeypatch.setattr(hub, 'read_ops', lambda slugs: calls.append(slugs) or [{'slug':'ashland'}])
    assert client.get('/ops/api/dashboard').json() == {'stands':[{'slug':'ashland'}]}
    assert calls == [['ashland']]
    user.active = False
    db.commit()
    assert client.get('/ops/api/dashboard').status_code == 401


def test_admin_modules_csrf_audit_and_revocation(client, db):
    token = sign_in(client)
    assert client.post('/admin/users/1/modules',data={'csrf':token}).status_code == 403
    db.get(User,2).role='admin'
    db.commit()
    token=sign_in(client,'manager@test.local')
    assert client.post('/admin/users/1/modules',data={}).status_code == 403
    assert client.post('/admin/users/1/modules',data={'csrf':token,'ops_access':'on'}).status_code == 422
    result=client.post('/admin/users/1/modules',data={'csrf':token,'ops_access':'on','ops_locations':'ashland'},follow_redirects=False)
    assert result.status_code == 303
    assert db.get(User,1).ops_locations == ['ashland']
    assert db.scalar(select(AdminAudit)).details['action']=='module_access'
    sign_in(client)
    assert client.get('/availability').status_code == 403
    assert client.get('/requests').status_code == 403
    assert client.get('/requests/new').status_code == 403
    assert client.post('/requests',data={}).status_code == 403
    assert 'My Availability' not in client.get('/').text
    assert 'Ops Dashboard' in client.get('/').text


def test_upstream_failures_and_overbroad_response(monkeypatch):
    from types import SimpleNamespace
    import httpx
    from fastapi import HTTPException
    monkeypatch.setattr(hub,'settings',lambda: SimpleNamespace(ops_backend_url='https://ops.example.com',ops_integration_key='a'*40))
    def response(url,**kwargs):
        assert kwargs['headers']['Authorization']=='Bearer '+'a'*40
        assert kwargs['follow_redirects'] is False
        return httpx.Response(200,json={'stands':[{'slug':'ashland'},{'slug':'hull-street'}]},request=httpx.Request('GET',url))
    monkeypatch.setattr(hub.httpx,'get',response)
    assert hub.read_ops(['ashland']) == [{'slug':'ashland'}]
    monkeypatch.setattr(hub.httpx,'get',lambda *a,**k: httpx.Response(500,request=httpx.Request('GET','https://ops.example.com')))
    with pytest.raises(HTTPException) as exc: hub.read_ops(['ashland'])
    assert exc.value.status_code == 503


def test_manifest_opens_hub_and_scripts_allowed(client):
    assert client.get('/static/manifest.webmanifest').json()['start_url']=='/'
    assert "script-src 'self'" in client.get('/login').headers['content-security-policy']
