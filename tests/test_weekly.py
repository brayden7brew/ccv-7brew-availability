from datetime import date, timedelta
import pytest
from sqlalchemy import select
from dateutil.rrule import rrulestr
from dateutil.parser import isoparse
from pydantic import ValidationError
from app.weekly import WeeklyInput, DayHours, event_plan, timeline, local_today
from app.models import WeeklySchedule, User, Audit, ManagedEvent
from app.workflow import decide
from app.config import settings
from test_portal import submit, sign_in, Fake

def week(mode='hours'):
    return [{'mode':mode,'start':'09:00' if mode=='hours' else '', 'end':'17:00' if mode=='hours' else ''} for _ in range(7)]

def profile(start, mode='hours'):
    return {'effective_date':start,'days':week(mode)}

def test_latest_week_is_indefinite_and_available_hours_inverted():
    plan=event_plan([profile('2030-01-06')],today=date(2030,1,1))
    assert len(plan)==14
    assert all(p['recurrence'].startswith('FREQ=WEEKLY;BYDAY=') and 'COUNT=' not in p['recurrence'] and p['type']==1 for p in plan)
    assert plan[0]['start_time'].endswith('00:00:00-05:00')
    assert plan[0]['end_time'].endswith('09:00:00-05:00')
    assert plan[1]['start_time'].endswith('17:00:00-05:00')
    assert plan[1]['end_time'].startswith('2030-01-07T00:00')

def test_new_approved_date_caps_previous_rules():
    plan=event_plan([profile('2030-01-06'),profile('2030-01-16','none')],today=date(2030,1,8))
    old=[p for p in plan if 'COUNT=' in p['recurrence']]
    new=[p for p in plan if 'COUNT=' not in p['recurrence']]
    assert old and len(new)==7
    for p in old:
        occurrences=list(rrulestr(p['recurrence'],dtstart=isoparse(p['start_time'])))
        assert all(date(2030,1,8)<=v.date()<date(2030,1,16) for v in occurrences)
    assert all(isoparse(p['start_time']).date()>=date(2030,1,16) for p in new)

def test_all_day_available_has_no_blocks_and_no_hours_blocks_whole_day():
    assert event_plan([profile('2030-01-06','all_day')],today=date(2030,1,1))==[]
    plan=event_plan([profile('2030-01-06','none')],today=date(2030,1,1))
    assert len(plan)==7 and all(p['all_day'] for p in plan)

def test_approved_future_timeline_handles_out_of_order_dates():
    plan=event_plan([profile('2030-02-01'),profile('2030-01-06'),profile('2030-01-20')],today=date(2030,1,1))
    assert sum(p['recurrence'].startswith('FREQ=WEEKLY;BYDAY=') and 'COUNT=' not in p['recurrence'] for p in plan)==14
    assert all(isoparse(p['start_time']).date()>=date(2030,2,1) for p in plan if p['recurrence'].startswith('FREQ=WEEKLY;BYDAY=') and 'COUNT=' not in p['recurrence'])

@pytest.mark.parametrize('data',[{'mode':'hours','start':'17:00','end':'09:00'}, {'mode':'hours','start':'','end':''}, {'mode':'preferred'}, {'mode':'hours','start':'9:00','end':'17:00'}])
def test_bad_daily_hours(data):
    with pytest.raises(ValidationError): DayHours(**data)

def test_requires_all_seven_days_and_no_end_date():
    with pytest.raises(ValidationError): WeeklyInput(effective_date=local_today()+timedelta(days=10), days=week()[:6])
    with pytest.raises(ValidationError): WeeklyInput(effective_date=local_today()+timedelta(days=10), days=week(),end_date='2030-01-01')

def test_pending_rejected_do_not_replace_approved(client, db):
    submit(client)
    assert timeline(db,1,True)==[]
    decide(db,db.get(User,2),1,'approve','',Fake())
    assert len(timeline(db,1,True))==1
    submit(client)
    decide(db,db.get(User,2),2,'reject','Not yet',Fake())
    assert [r.change_id for r in timeline(db,1,True)]==[1]

def test_next_approval_replaces_same_start_date(client,db):
    submit(client)
    decide(db,db.get(User,2),1,'approve','',Fake())
    submit(client)
    decide(db,db.get(User,2),2,'approve','',Fake())
    assert [r.change_id for r in timeline(db,1,True)]==[2]
    assert len(db.scalars(select(WeeklySchedule)).all())==2

def test_form_is_sunday_to_saturday_positive_hours_only(client):
    sign_in(client)
    html=client.get('/requests/new').text
    assert html.index('Sunday</legend>')<html.index('Saturday</legend>')
    assert 'name="effective_date"' in html
    for removed in ['name="end_time"','name="end_date"','name="type"','name="weeks"','Preferred</option>','Unavailable</option>']:
        assert removed not in html
    assert html.count('<fieldset')==7

def test_partial_batch_stops_and_journals(client,db,monkeypatch):
    from app.wiw import WIWError
    monkeypatch.setattr(settings(),'dry_run',False)
    submit(client)
    class Partial(Fake):
        def weekly_operation(self,change,operation):
            if self.writes==1:
                self.writes+=1
                raise WIWError('Timeout')
            return super().weekly_operation(change,operation)
    provider=Partial()
    change=decide(db,db.get(User,2),1,'approve','',provider)
    assert change.status=='needs_reconciliation'
    assert len(db.scalars(select(ManagedEvent)).all())==1
    assert timeline(db,1,False)==[]
    events=list(db.scalars(select(Audit.event)))
    assert events.count('operation_started')==2 and events.count('operation_succeeded')==1

def test_hours_can_end_at_midnight():
    d=DayHours(mode='hours',start='22:00',end='00:00')
    from app.weekly import blocked
    assert blocked(d.model_dump())==[(0,1320)]

def test_full_live_replacement_preserves_approved_timeline(client,db,monkeypatch):
    from app.models import Change
    from test_portal import token
    import copy
    monkeypatch.setattr(settings(),'dry_run',False)
    class MemoryWIW:
        def __init__(self): self.events={}; self.counter=100; self.calls=[]
        def read(self,*args): return {'availabilityevents':copy.deepcopy(list(self.events.values()))}
        def get(self,event_id,*args): return copy.deepcopy(self.events[event_id])
        def weekly_operation(self,change,op):
            self.calls.append(copy.deepcopy(op))
            if op['action']=='delete':
                del self.events[op['event_id']]
                return {'success':True}
            self.counter+=1
            event={'id':self.counter,'user_id':1,'account_id':10,**op['payload']}
            self.events[self.counter]=event
            return {'availabilityevent':copy.deepcopy(event)}
    provider=MemoryWIW()
    monkeypatch.setattr('app.main.WIW',lambda:provider)
    submit(client)
    decide(db,db.get(User,2),1,'approve','',provider)
    first_ids=set(provider.events)
    csrf=sign_in(client)
    effective=(local_today()+timedelta(days=45)).isoformat()
    data={'csrf':csrf,'action':'weekly','effective_date':effective}
    for i in range(7): data[f'day_{i}_mode']='none'
    assert client.post('/requests',data=data).status_code==200
    decide(db,db.get(User,2),2,'approve','',provider)
    assert db.get(Change,2).status=='applied'
    assert first_ids.isdisjoint(provider.events)
    assert len(timeline(db,1,False))==2
    assert len(db.scalars(select(ManagedEvent).where(ManagedEvent.active.is_(True))).all())==len(provider.events)
    capped=[e for e in provider.events.values() if 'COUNT=' in e['recurrence']]
    assert capped
    for e in capped:
        assert all(t.date()<date.fromisoformat(effective) for t in rrulestr(e['recurrence'],dtstart=isoparse(e['start_time'])))
    audit=db.scalars(select(Audit).where(Audit.change_id==2,Audit.event=='approved')).one()
    assert {e['id'] for e in audit.details['managed_event_snapshots']}==first_ids

def test_reconciliation_requires_complete_week(client,db,monkeypatch):
    from app.weekly_workflow import reconcile_weekly
    from app.models import Change
    monkeypatch.setattr(settings(),'dry_run',False)
    submit(client)
    decide(db,db.get(User,2),1,'approve','',Fake(fail=True))
    change=db.get(Change,1)
    with pytest.raises(ValueError):
        reconcile_weekly(db,db.get(User,2),change,'applied','Inspected',Fake())
    assert change.status=='needs_reconciliation'
    approval=db.scalars(select(Audit).where(Audit.event=='approved')).one()
    events=[{'id':500+i,'user_id':1,'account_id':10,**op['payload']}
            for i,op in enumerate(approval.details['operations']) if op['action']=='create']
    reconcile_weekly(db,db.get(User,2),change,'applied','Verified all events',Fake({'availabilityevents':events}))
    db.commit()
    assert change.status=='reconciled_applied'
    assert len(timeline(db,1,False))==1
    assert len(db.scalars(select(ManagedEvent)).all())==2


def test_unmanaged_preferences_are_not_deleted(client,db,monkeypatch):
    from fastapi import HTTPException
    existing={'id':77,'user_id':1,'account_id':10,'type':1,'start_time':'2030-01-01T00:00:00-05:00','end_time':'2030-01-02T00:00:00-05:00'}
    provider=Fake({'availabilityevents':[existing]})
    monkeypatch.setattr('app.main.WIW',lambda:provider)
    submit(client)
    with pytest.raises(HTTPException) as exc:
        decide(db,db.get(User,2),1,'approve','',provider)
    assert exc.value.status_code==409 and provider.writes==0


def test_stale_server_template_never_shows_empty_submittable_week():
    from app.main import templates, cfg
    html=templates.env.get_template('new.html').render(cfg=cfg, csrf='test')
    assert 'Restart the portal' in html
    assert 'Submit weekly availability' not in html
    assert '<form method="post" action="/requests"' not in html


def test_repeat_weekday_matches_every_occurrence():
    profile = {'effective_date': '2030-01-02', 'days': [{'mode': 'none'} for _ in range(7)]}
    plan = event_plan([profile], today=date(2030, 1, 1))
    assert len(plan) == 7
    for payload in plan:
        start = isoparse(payload['start_time'])
        expected = ('MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU')[start.weekday()]
        assert payload['recurrence'] == 'FREQ=WEEKLY;BYDAY=' + expected
        occurrences = list(rrulestr(payload['recurrence'] + ';COUNT=3', dtstart=start))
        assert occurrences[0] == start
        assert all(item.weekday() == start.weekday() for item in occurrences)
