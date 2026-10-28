from sqlalchemy import select,func
from app.models import User,Change,TimeOffSettings
from test_portal import sign_in,submit


def test_admin_preview_uses_employee_permissions_without_changing_account(client,db):
    submit(client)
    employee_request=db.scalar(select(Change.id))
    db.get(User,2).role='admin';db.get(User,2).time_off_access=True
    db.add(TimeOffSettings(id=1,enabled=True,everyone=False));db.commit()
    csrf=sign_in(client,'manager@test.local')
    response=client.post('/employee-view',data={'csrf':csrf})
    assert response.status_code==200
    assert 'Exit employee view' in response.text and 'My Requests' in response.text
    assert 'href="/admin"' not in response.text and 'href="/ops"' not in response.text
    assert db.get(User,2).role=='admin'
    assert client.get('/admin').status_code==403
    assert client.get(f'/requests/{employee_request}').status_code==404
    assert f'href="/requests/{employee_request}"' not in client.get('/requests').text
    assert client.get('/ops').status_code==403
    assert client.get('/requests/new').status_code==200
    assert client.get('/time-off').status_code==200
    before=db.scalar(select(func.count(Change.id)))
    assert client.post('/time-off',data={'csrf':csrf}).status_code==403
    assert client.post('/requests',data={'csrf':csrf}).status_code==403
    assert db.scalar(select(func.count(Change.id)))==before
    response=client.post('/employee-view',data={'csrf':csrf,'mode':'exit'})
    assert response.status_code==200 and 'href="/admin"' in response.text
    assert 'Exit employee view' not in response.text
    assert client.get('/admin').status_code==200


def test_employee_cannot_enable_preview_and_csrf_is_required(client,db):
    csrf=sign_in(client)
    assert client.post('/employee-view',data={'csrf':csrf}).status_code==403
    db.get(User,2).role='admin';db.commit()
    sign_in(client,'manager@test.local')
    assert client.post('/employee-view',data={}).status_code==403


def test_preview_rechecks_role_and_cannot_preserve_admin_access(client,db):
    db.get(User,2).role='admin';db.commit()
    csrf=sign_in(client,'manager@test.local')
    client.post('/employee-view',data={'csrf':csrf})
    db.get(User,2).role='employee';db.commit()
    response=client.get('/')
    assert 'Exit employee view' not in response.text
    assert client.get('/admin').status_code==403
