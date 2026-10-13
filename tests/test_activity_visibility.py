import pytest
from sqlalchemy import select
from app.models import Audit, Change, User
from test_portal import submit, sign_in

@pytest.mark.parametrize('role,email', [('employee','employee@test.local'), ('manager','manager@test.local'), ('admin','manager@test.local')])
def test_only_admin_gets_full_activity(client, db, role, email):
    submit(client)
    change = db.scalar(select(Change))
    db.add(Audit(change_id=change.id, actor_id=2, event='write_uncertain',
                 details={'reason':'PRIVATE_OPERATION_PAYLOAD'}))
    if role == 'admin':
        db.get(User,2).role='admin'
    db.commit()
    sign_in(client,email)
    html=client.get(f'/requests/{change.id}').text
    activity=html.split('<h2>Request activity</h2>')[1]
    assert 'Request submitted' in activity
    if role == 'admin':
        assert 'Technical details' in activity
        assert 'PRIVATE_OPERATION_PAYLOAD' in activity
        assert 'Update needs attention' in activity
    else:
        assert 'Pending approval' in activity
        assert 'Technical details' not in activity
        assert 'PRIVATE_OPERATION_PAYLOAD' not in html
        assert 'Update needs attention' not in activity

@pytest.mark.parametrize('event,status,label', [('approved','applied','Request approved'), ('rejected','rejected','Request denied')])
def test_employee_sees_plain_decision_history(client,db,event,status,label):
    submit(client)
    change=db.scalar(select(Change))
    change.status=status
    db.add(Audit(change_id=change.id,actor_id=2,event=event,details={'private':'DECISION_INTERNALS'}))
    db.add(Audit(change_id=change.id,actor_id=2,event='operation_succeeded',details={'private':'WRITE_INTERNALS'}))
    db.commit()
    activity=client.get(f'/requests/{change.id}').text.split('<h2>Request activity</h2>')[1]
    assert label in activity and 'Request submitted' in activity
    assert 'Pending approval' not in activity
    assert 'INTERNALS' not in activity and 'Update step completed' not in activity
