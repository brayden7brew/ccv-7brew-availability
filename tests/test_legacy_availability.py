from datetime import date
from dateutil.parser import parse
from dateutil.rrule import rrulestr
import pytest
from app.legacy_availability import handover


def event(**overrides):
    return dict(id=77, user_id=1, account_id=10, type=1,
                start_time='2026-09-01T05:00:00-04:00', end_time='2026-09-01T10:00:00-04:00',
                recurrence='FREQ=WEEKLY;BYDAY=TU', **overrides)


def test_cap_series_keeps_every_prior_occurrence():
    original = event()
    operations, retained = handover([original], date(2026,10,15))
    assert operations[0]['action']=='update'
    payload=operations[0]['payload']
    assert payload['start_time']==original['start_time']
    old=rrulestr(original['recurrence'],dtstart=parse(original['start_time']))
    expected=[v for v in old.between(parse('2026-09-01T00:00:00-04:00'),parse('2026-10-15T00:00:00-04:00'))]
    assert list(rrulestr(payload['recurrence'],dtstart=parse(payload['start_time'])))==expected
    assert retained[0]['id']==77


def test_completed_and_future_entries():
    past=event(); past['recurrence']='FREQ=WEEKLY;COUNT=2;BYDAY=TU'
    future=event(); future['start_time']='2026-11-03T05:00:00-05:00'; future['end_time']='2026-11-03T10:00:00-05:00'
    operations,retained=handover([past,future],date(2026,10,15))
    assert operations==[{'action':'delete','event_id':77}]
    assert retained==[past]


def test_one_off_crossing_boundary_is_shortened_not_deleted():
    one=event(); one.update(recurrence='',start_time='2026-10-14T22:00:00-04:00',end_time='2026-10-15T02:00:00-04:00')
    operations,retained=handover([one],date(2026,10,15))
    assert operations[0]['action']=='update'
    assert retained[0]['end_time']=='2026-10-15T00:00:00-04:00'


def test_unparseable_or_crossing_recurrence_fails_without_a_plan():
    bad=event(); bad['recurrence']='not a rule'
    with pytest.raises(ValueError): handover([bad],date(2026,10,15))
    crossing=event(); crossing.update(start_time='2026-10-14T22:00:00-04:00',end_time='2026-10-15T02:00:00-04:00',recurrence='FREQ=WEEKLY;BYDAY=WE')
    with pytest.raises(ValueError): handover([crossing],date(2026,10,15))


def test_default_live_handover_and_later_approval(client, db, monkeypatch):
    import copy
    from datetime import timedelta
    from sqlalchemy import select
    from fastapi import HTTPException
    from app.models import User, Change, ManagedEvent, Audit
    from app.config import settings
    from app.workflow import decide
    from app.weekly import local_today
    from test_portal import submit, sign_in
    monkeypatch.setattr(settings(),'dry_run',False)
    today=local_today()
    original=event()
    original.update(start_time=f'{today.isoformat()}T05:00:00-04:00',
                    end_time=f'{today.isoformat()}T10:00:00-04:00',recurrence='FREQ=DAILY')
    future = {**original, 'id':78, 'recurrence':'',
              'start_time':f'{(today+timedelta(days=30)).isoformat()}T05:00:00-04:00',
              'end_time':f'{(today+timedelta(days=30)).isoformat()}T10:00:00-04:00'}
    past = {**original, 'id':79, 'recurrence':'',
            'start_time':f'{(today-timedelta(days=1)).isoformat()}T05:00:00-04:00',
            'end_time':f'{(today-timedelta(days=1)).isoformat()}T10:00:00-04:00'}
    class Provider:
        def __init__(self): self.events=copy.deepcopy({77:original,78:future,79:past}); self.calls=[]; self.counter=100
        def read(self,*args): return {'availabilityevents':copy.deepcopy(list(self.events.values()))}
        def get(self,key,*args): return copy.deepcopy(self.events[key])
        def weekly_operation(self,change,op):
            self.calls.append(copy.deepcopy(op))
            if op['action']=='update':
                self.events[op['event_id']].update(op['payload'])
                return {'availabilityevents':[copy.deepcopy(self.events[op['event_id']])]}
            if op['action']=='delete':
                del self.events[op['event_id']]
                return {'success':True}
            assert 78 not in self.events, 'Future overlaps must be removed before creation'
            self.counter+=1
            result={'id':self.counter,'user_id':1,'account_id':10,**op['payload']}
            self.events[self.counter]=result
            return {'availabilityevent':copy.deepcopy(result)}
    provider=Provider()
    monkeypatch.setattr('app.main.WIW',lambda:provider)
    submit(client)
    sign_in(client,'manager@test.local')
    response=client.get('/requests/1')
    assert 'Existing When I Work availability' in response.text
    assert 'name="replace_existing"' not in response.text
    assert 'Approving automatically replaces' in response.text
    assert "script-src 'self'" in response.headers['content-security-policy']
    change=decide(db,db.get(User,2),1,'approve','',provider)
    assert change.status=='applied'
    assert provider.calls[0]['action']=='update'
    assert all(op.get('rfc_dates') for op in provider.calls if op['action'] in ('create','update'))
    assert provider.events[77]['start_time']==original['start_time']
    assert 'COUNT=' in provider.events[77]['recurrence']
    assert provider.events[79] == past
    assert 78 not in provider.events
    assert [op['action'] for op in provider.calls[:2]] == ['update','delete']
    assert 77 not in set(db.scalars(select(ManagedEvent.event_id)))
    submit(client)
    decide(db,db.get(User,2),2,'approve','',provider)
    assert db.get(Change,2).status=='applied'
    assert not any(op['action']=='delete' and op['event_id']==77 for op in provider.calls)


def test_update_failure_stops_before_creating_new_hours(client,db,monkeypatch):
    from app.config import settings
    from app.models import User
    from app.workflow import decide
    from test_portal import submit, Fake
    monkeypatch.setattr(settings(),'dry_run',False)
    provider=Fake({'availabilityevents':[event()]},fail=True)
    monkeypatch.setattr('app.main.WIW',lambda:provider)
    submit(client)
    result=decide(db,db.get(User,2),1,'approve','',provider,replace_existing=True)
    assert result.status=='needs_reconciliation' and provider.writes==1
