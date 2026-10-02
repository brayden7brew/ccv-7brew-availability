import pytest
from sqlalchemy import select
from fastapi import HTTPException
from app.models import User, Scope, AdminAudit, Change
from app.workflow import decide
from app.weekly import week_totals
from test_portal import sign_in, submit, Fake

def test_admin_menu_and_scope_changes(client, db):
    sign_in(client)
    assert client.get('/admin').status_code == 403
    db.get(User,2).role='admin'
    db.commit()
    csrf=sign_in(client,'manager@test.local')
    assert 'People &amp; approval locations' in client.get('/admin').text
    assert client.post('/admin/users/1',data={'role':'manager','location':'North','scopes':'North'}).status_code==403
    response=client.post('/admin/users/1',data={'csrf':csrf,'role':'manager','location':'North','scopes':['North','South','North']})
    assert response.status_code==200
    db.expire_all()
    assert db.get(User,1).role=='manager'
    assert set(db.scalars(select(Scope.location).where(Scope.manager_id==1)))=={'North','South'}
    assert db.scalar(select(AdminAudit)).actor_id==2
    response=client.post('/admin/users/1',data={'csrf':csrf,'role':'admin','location':'North','scopes':'North'})
    assert response.status_code==200

def test_admin_cannot_approve_other_location_or_move_pending_employee(client,db):
    submit(client)
    db.get(User,2).role='admin'
    db.commit()
    csrf=sign_in(client,'manager@test.local')
    assert client.post('/admin/users/1',data={'csrf':csrf,'role':'employee','location':'South','scopes':''}).status_code==409
    scope=db.scalar(select(Scope).where(Scope.manager_id==2))
    scope.location='Elsewhere'
    db.commit()
    with pytest.raises(HTTPException) as exc:
        decide(db,db.get(User,2),1,'approve','',Fake())
    assert exc.value.status_code==404
    assert db.get(Change,1).status=='pending'

@pytest.mark.parametrize('days,expected', [
    ([{'mode':'all_day'}]*7, ('126h','0h')),
    ([{'mode':'none'}]*7, ('0h','126h')),
    ([{'mode':'hours','start':'09:00','end':'17:30'}]*5+[{'mode':'none'}]*2, ('42h 30m','83h 30m')),
    ([{'mode':'hours','start':'23:00','end':'00:00'}]*7, ('0h','126h')),
])
def test_weekly_totals(days,expected):
    totals=week_totals({'days':days})
    assert (totals['available'],totals['unavailable'])==expected
