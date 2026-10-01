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
