import pytest
from app.models import Change, Audit
from sqlalchemy import select
from test_portal import submit, sign_in


@pytest.mark.parametrize('status', ['applied','applying','needs_reconciliation','rejected','approved_dry_run','conflict'])
def test_repeated_decision_redirects_to_real_status_without_writes(client, db, monkeypatch, status):
    submit(client)
    change=db.get(Change,1)
    change.status=status
    change.manager_id=2
    db.commit()
    csrf=sign_in(client,'manager@test.local')
    before=len(db.scalars(select(Audit)).all())
    def unexpected(*args,**kwargs): raise AssertionError('Repeated decision must not contact WIW')
    monkeypatch.setattr('app.wiw.WIW.read',unexpected)
    monkeypatch.setattr('app.wiw.WIW.weekly_operation',unexpected)
    response=client.post('/requests/1/decision',data={'csrf':csrf,'decision':'approve','note':''},follow_redirects=False)
    assert response.status_code==303 and response.headers['location']=='/requests/1'
    assert db.get(Change,1).status==status
    assert len(db.scalars(select(Audit)).all())==before
    assert client.get(response.headers['location']).status_code==200


def test_duplicate_redirect_does_not_bypass_location_access(client, db):
    submit(client)
    db.get(Change,1).status='applied';db.commit()
    csrf=sign_in(client,'other@test.local')
    response=client.post('/requests/1/decision',data={'csrf':csrf,'decision':'approve'},follow_redirects=False)
    assert response.status_code==404
