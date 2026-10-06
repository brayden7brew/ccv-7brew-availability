from datetime import timedelta
from sqlalchemy import select
from app.models import Change, now
from app.config import settings
from app.request_limits import request_usage
from test_portal import submit, sign_in

def test_boundary_and_status_independent_count(client, db, monkeypatch):
    submit(client)
    item = db.scalar(select(Change))
    instant = now()
    item.created = instant - timedelta(days=30)
    db.commit()
    assert request_usage(db, item.employee_id, instant)['count'] == 0
    item.created += timedelta(seconds=1)
    item.status = 'rejected'
    db.commit()
    monkeypatch.setattr(settings(), 'availability_request_limit_30_days', 1)
    usage = request_usage(db, item.employee_id, instant)
    assert usage['count'] == 1 and usage['blocked'] and usage['remaining'] == 0
    assert usage['reset'] == instant + timedelta(seconds=1)
    assert request_usage(db, 2, instant)['count'] == 0

def test_limit_blocks_direct_post_without_creating_request(client, db, monkeypatch):
    submit(client)
    monkeypatch.setattr(settings(), 'availability_request_limit_30_days', 1)
    csrf = sign_in(client)
    form = {'csrf':csrf, 'action':'weekly', 'effective_date':(now()+timedelta(days=30)).strftime('%Y-%m-%d')}
    form.update({f'day_{i}_mode':'all_day' for i in range(7)})
    assert 'Request limit reached' in client.get('/requests/new').text
    response = client.post('/requests', data=form)
    assert response.status_code == 429
    assert len(list(db.scalars(select(Change)))) == 1
    monkeypatch.setattr(settings(), 'availability_request_limit_30_days', 0)
    assert client.post('/requests', data=form).status_code == 200


def test_admin_extra_request_is_one_use_and_survives_invalid_submission(client, db, monkeypatch):
    from app.models import User, AdminAudit
    submit(client)
    monkeypatch.setattr(settings(), 'availability_request_limit_30_days', 1)
    token = sign_in(client)
    assert client.post('/admin/users/1/extra-request', data={'csrf':token}).status_code == 403
    db.get(User,2).role='admin'; db.commit()
    token = sign_in(client,'manager@test.local')
    assert client.post('/admin/users/1/extra-request', data={'csrf':'wrong'}).status_code == 403
    assert client.post('/admin/users/1/extra-request', data={'csrf':token}).status_code == 200
    db.refresh(db.get(User,1))
    assert db.get(User,1).extra_request_credits == 1
    assert db.scalar(select(AdminAudit).where(AdminAudit.target_id==1)).details['event']=='extra_request_granted'
    usage=request_usage(db,1)
    assert not usage['blocked'] and usage['remaining']==1
    token=sign_in(client)
    form={'csrf':token,'action':'weekly','effective_date':(now()+timedelta(days=30)).strftime('%Y-%m-%d')}
    form.update({f'day_{i}_mode':'none' for i in range(7)})
    assert client.post('/requests',data=form).status_code==422
    db.refresh(db.get(User,1)); assert db.get(User,1).extra_request_credits==1
    form.update({f'day_{i}_mode':'all_day' for i in range(7)})
    assert client.post('/requests',data=form).status_code==200
    db.refresh(db.get(User,1)); assert db.get(User,1).extra_request_credits==0
    assert db.scalars(select(Change).order_by(Change.id.desc())).first().request_limit_exempt
    assert client.post('/requests',data=form).status_code==429
    original=db.scalars(select(Change).order_by(Change.id)).first()
    original.created=now()-timedelta(days=31); db.commit()
    assert request_usage(db,1)['remaining']==1


def test_extra_credit_is_saved_until_regular_allowance_exhausted(client,db,monkeypatch):
    from app.models import User
    db.get(User,1).extra_request_credits=1; db.commit()
    monkeypatch.setattr(settings(),'availability_request_limit_30_days',2)
    submit(client)
    db.refresh(db.get(User,1))
    assert db.get(User,1).extra_request_credits==1
    assert not db.scalar(select(Change)).request_limit_exempt
