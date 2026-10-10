from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import select
from app.models import Change, now
from app.config import settings
from app.request_limits import request_usage
from test_portal import submit, sign_in

@pytest.mark.parametrize("year,month", [(2026, 3), (2026, 11), (2026, 12), (2028, 2)])
def test_calendar_month_boundary_and_status_independent_count(client, db, monkeypatch, year, month):
    submit(client)
    item = db.scalar(select(Change))
    zone = ZoneInfo("America/New_York")
    beginning = datetime(year, month, 1, tzinfo=zone).astimezone(timezone.utc)
    next_local = (datetime(year + 1, 1, 1, tzinfo=zone) if month == 12
                  else datetime(year, month + 1, 1, tzinfo=zone))
    reset = next_local.astimezone(timezone.utc)
    monkeypatch.setattr(settings(), 'availability_request_limit_30_days', 1)
    item.created = beginning - timedelta(microseconds=1)
    db.commit()
    assert request_usage(db, item.employee_id, beginning)['count'] == 0
    item.created = beginning
    item.status = 'rejected'
    db.commit()
    usage = request_usage(db, item.employee_id, beginning)
    assert usage['count'] == 1 and usage['blocked'] and usage['remaining'] == 0
    assert usage['reset'] == reset
    assert request_usage(db, item.employee_id, reset - timedelta(microseconds=1))['blocked']
    assert request_usage(db, item.employee_id, reset)['count'] == 0
    assert request_usage(db, item.employee_id, reset)['remaining'] == 1
    assert request_usage(db, 2, beginning)['count'] == 0
    item.created = reset
    db.commit()
    assert request_usage(db, item.employee_id, reset - timedelta(microseconds=1))['count'] == 0


def test_limit_blocks_direct_post_without_creating_request(client, db, monkeypatch):
    submit(client)
    monkeypatch.setattr(settings(), 'availability_request_limit_30_days', 1)
    csrf = sign_in(client)
    form = {'csrf':csrf, 'action':'weekly', 'effective_date':(now()+timedelta(days=30)).strftime('%Y-%m-%d')}
    form.update({f'day_{i}_mode':'all_day' for i in range(7)})
    page = client.get('/requests/new').text
    assert 'Request limit reached' in page
    assert 'this month' in page and 'first of each month' in page
    assert 'last 30 days' not in page
    response = client.post('/requests', data=form)
    assert response.status_code == 429
    assert "requests this calendar month" in response.text
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

def test_unused_extra_credit_carries_to_next_month(client, db, monkeypatch):
    from app.models import User
    monkeypatch.setattr(settings(), 'availability_request_limit_30_days', 1)
    submit(client)
    item = db.scalar(select(Change))
    item.created = datetime(2026, 10, 31, 18, tzinfo=timezone.utc)
    db.get(User, 1).extra_request_credits = 1
    db.commit()
    before = request_usage(db, 1, datetime(2026, 11, 1, 3, 59, tzinfo=timezone.utc))
    after = request_usage(db, 1, datetime(2026, 11, 1, 4, tzinfo=timezone.utc))
    assert before['use_credit'] and before['remaining'] == 1
    assert after['count'] == 0 and after['remaining'] == 2
    assert after['credits'] == 1 and not after['use_credit']
