import copy
import pytest
from sqlalchemy import select
from app.config import settings
from app.models import User, Change, WeeklySchedule
from app.workflow import decide
from app.create_recovery import resume_verified_creations
from app.wiw import WIWError
from test_portal import submit


@pytest.mark.parametrize('case', ['success', 'extra', 'changed', 'missing', 'timeout', 'unauthorized', 'again409', 'final_mismatch'])
def test_resume_only_verified_create_prefix(client, db, monkeypatch, case):
    monkeypatch.setattr(settings(), 'dry_run', False)
    monkeypatch.setattr(settings(), 'wiw_mode', 'live')
    class Provider:
        def __init__(self): self.events = {}; self.calls = []; self.recovering = False
        def read(self, *args):
            events = list(self.events.values())
            if case == 'final_mismatch' and len(self.calls) == 3: events = []
            return {'availabilityevents': copy.deepcopy(events)}
        def get(self, key, *args):
            if key not in self.events: raise WIWError('Missing', reason='http_error', http_status=404)
            return copy.deepcopy(self.events[key])
        def weekly_operation(self, change, op):
            self.calls.append(copy.deepcopy(op))
            if len(self.calls) == 2 or (self.recovering and case == 'again409'):
                raise WIWError('Timeout' if case == 'timeout' else 'WIW returned HTTP 409 (WIW code 4090). Conflict')
            event = {'id': 100 + len(self.calls), 'user_id': 1, 'account_id': 10, **op['payload']}
            self.events[event['id']] = event
            return {'availabilityevent': copy.deepcopy(event)}
    provider = Provider()
    monkeypatch.setattr('app.main.WIW', lambda: provider)
    submit(client)
    actor = db.get(User, 2)
    result = decide(db, actor, 1, 'approve', '', provider)
    assert result.status == 'needs_reconciliation' and len(provider.calls) == 2
    if case == 'extra': provider.events[999] = {**provider.events[101], 'id': 999}
    if case == 'changed': provider.events[101]['notes'] = 'External change'
    if case == 'missing': provider.events.clear()
    if case == 'unauthorized': actor = db.get(User, 3)
    provider.recovering = True
    if case in ('success', 'again409', 'final_mismatch'):
        result = resume_verified_creations(db, actor, 1, provider)
        assert len(provider.calls) == 3
        assert provider.calls[2] == provider.calls[1]  # Never repeats the successful Sunday.
        assert result.status == ('applied' if case == 'success' else 'needs_reconciliation')
        assert len(db.scalars(select(WeeklySchedule)).all()) == (1 if case == 'success' else 0)
        with pytest.raises(ValueError): resume_verified_creations(db, actor, 1, provider)
        assert len(provider.calls) == 3
    else:
        with pytest.raises((ValueError, WIWError)): resume_verified_creations(db, actor, 1, provider)
        assert len(provider.calls) == 2
        assert db.get(Change, 1).status == 'needs_reconciliation'
