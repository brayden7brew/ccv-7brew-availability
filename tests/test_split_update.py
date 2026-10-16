import copy
from datetime import date
from types import SimpleNamespace
import httpx
import pytest
from app.legacy_availability import handover
from app.event_identity import finite_occurrences
from app.wiw import WIW, WIWError


def original():
    return dict(id=77, user_id=1, account_id=10, type=1, all_day=True,
        start_time='2026-08-13T00:00:00-04:00', end_time='2026-08-13T23:59:59-04:00',
        recurrence='FREQ=WEEKLY;UNTIL=20270702T035959Z;BYDAY=TH,SA')


def split():
    source = original()
    return [{**source, 'recurrence':'FREQ=WEEKLY;UNTIL=20261017T035959Z;BYDAY=TH,SA'},
        {**source, 'id':78, 'start_time':'2026-10-17T00:00:00-04:00',
         'end_time':'2026-10-17T23:59:59-04:00', 'recurrence':'FREQ=WEEKLY;COUNT=24;BYDAY=TH,SA'}]


def live(monkeypatch):
    from app.config import settings
    cfg = settings()
    monkeypatch.setattr(cfg, 'dry_run', False)
    monkeypatch.setattr(cfg, 'wiw_mode', 'live')
    monkeypatch.setattr(cfg, 'wiw_account_id', 10)
    monkeypatch.setattr('app.wiw_tokens.service_token', lambda:'test-token')


@pytest.mark.parametrize('bad', ['none', 'extended', 'missing', 'duplicate', 'other_employee'])
def test_readback_verifies_entire_split_not_only_response(monkeypatch, bad):
    live(monkeypatch)
    op = handover([original()], date(2026,11,1))[0][0]
    assert 'COUNT=' not in op['payload']['recurrence']
    assert 'UNTIL=20261101T035959Z' in op['payload']['recurrence']
    events = split()
    events[1]['recurrence'] = 'FREQ=WEEKLY;UNTIL=20261101T035959Z;BYDAY=TH,SA'
    if bad == 'extended': events[1]['recurrence'] = 'FREQ=WEEKLY;COUNT=24;BYDAY=TH,SA'
    if bad == 'missing': events[1]['start_time'] = '2026-10-24T00:00:00-04:00'; events[1]['end_time'] = '2026-10-24T23:59:59-04:00'
    if bad == 'duplicate': events[1] = {**events[0], 'id':78}
    if bad == 'other_employee': events[1]['user_id'] = 999
    def handle(req):
        if req.method == 'PUT': return httpx.Response(200, json={'availabilityevents':events})
        return httpx.Response(200, json={'availabilityevent':next(e for e in events if str(e['id']) == req.url.path.split('/')[-1])})
    api = WIW(httpx.MockTransport(handle))
    change = SimpleNamespace(action='weekly',status='applying',manager_id=2,dry_run=False,wiw_user_id=1)
    if bad == 'none':
        assert api.weekly_operation(change, op)['availabilityevents'] == events
    else:
        with pytest.raises(WIWError): api.weekly_operation(change, op)


def test_split_comparison_preserves_dst_wall_clock_and_rejects_infinite_tail():
    event = {**original(), 'start_time':'2026-10-25T00:00:00-04:00',
             'end_time':'2026-10-25T23:59:59-04:00', 'recurrence':'FREQ=WEEKLY;BYDAY=SU;COUNT=3'}
    occurrences = list(finite_occurrences([event]))
    assert occurrences[1][1:3] == ('2026-11-01T04:00:00+00:00', '2026-11-02T05:00:00+00:00')
    with pytest.raises(ValueError): finite_occurrences([{**event,'recurrence':'FREQ=WEEKLY;BYDAY=SU'}])


@pytest.mark.parametrize('changed', [False, True])
def test_repair_first_split_update_preserves_october_and_completes(db, monkeypatch, changed):
    from sqlalchemy import select
    from app.models import Change, Audit, User, ManagedEvent, WeeklySchedule
    from app.split_recovery import repair_split_update
    live(monkeypatch)
    monkeypatch.setattr('app.split_recovery.local_today', lambda:date(2026,10,16))
    days = [{'mode':'all_day','start':'','end':''} for _ in range(7)]
    days[0] = {'mode':'none','start':'','end':''}
    before = {'availabilityevents':[original()], 'timeline_ids':[]}
    change = Change(employee_id=1, wiw_user_id=1, location='North', action='weekly',
        proposed={'effective_date':'2026-11-01','days':days}, before=before,
        read_start='2026-10-16T00:00:00-04:00',read_end='2027-11-02T00:00:00-04:00',
        status='needs_reconciliation', manager_id=2, dry_run=False)
    db.add(change); db.flush()
    op = {'action':'update','event_id':77, 'payload':{**original(),'recurrence':'FREQ=WEEKLY;BYDAY=TH,SA;COUNT=24'}}
    for event,details in [('approved', {'operations':[op], 'pre_write_state':before}),
        ('operation_started', {'index':0, 'operation':op}), ('write_uncertain', {'index':0})]:
        db.add(Audit(change_id=change.id,actor_id=2,event=event,details=details))
    db.commit()
    class Provider:
        def __init__(self): self.events={e['id']:e for e in split()}; self.writes=[]
        def read(self,*a): return {'availabilityevents':copy.deepcopy(list(self.events.values()))}
        def get(self,key,*a): return copy.deepcopy(self.events[key])
        def weekly_operation(self,c,operation):
            self.writes.append(operation)
            if operation['action']=='update':
                assert operation['event_id']==78
                self.events[78].update(operation['payload'])
                return {'availabilityevents':[self.get(78)]}
            assert operation['action']=='create'
            key = 100 + len(self.writes)
            self.events[key] = {'id':key,'user_id':1,'account_id':10,**operation['payload']}
            return {'availabilityevent':self.get(key)}
    provider = Provider()
    if changed:
        provider.events[78]['recurrence']='FREQ=WEEKLY;BYDAY=SA;COUNT=24'
        with pytest.raises(ValueError, match='before the requested date changed'):
            repair_split_update(db, db.get(User,2), change.id, provider)
        assert not provider.writes
    else:
        result = repair_split_update(db, db.get(User,2), change.id, provider)
        assert result.status == 'applied'
        assert provider.events[77] == split()[0]
        assert 'UNTIL=20261101T035959Z' in provider.events[78]['recurrence']
        assert all(e.event_id not in (77,78) for e in db.scalars(select(ManagedEvent)))
        assert db.scalar(select(WeeklySchedule)).effective_date == date(2026,11,1)
        with pytest.raises(ValueError): repair_split_update(db, db.get(User,2), change.id, provider)


def test_dispatch_retains_both_verified_split_records_and_detects_extra(db):
    from app.models import Change, User, Audit, WeeklySchedule
    from app.weekly_workflow import dispatch_weekly
    from sqlalchemy import select
    source = original()
    op, retained = handover([source], date(2026,11,1))
    expected = split()
    expected[1]['recurrence'] = 'FREQ=WEEKLY;UNTIL=20261101T035959Z;BYDAY=TH,SA'
    for extra in (False, True):
        change = Change(employee_id=1, wiw_user_id=1, location='North', action='weekly',
            proposed={'effective_date':'2026-11-01','days':[]}, before={},
            read_start='2026-10-17T00:00:00-04:00',read_end='2027-11-02T00:00:00-04:00',
            status='applying', manager_id=2, dry_run=False)
        db.add(change); db.commit()
        class Provider:
            def weekly_operation(self,c,operation): return {'availabilityevents':expected}
            def read(self,*a):
                # The old fragment ends outside the read window and is omitted.
                events = [expected[1]]
                if extra: events += [{**expected[1], 'id':99}]
                return {'availabilityevents':events}
        result = dispatch_weekly(db, db.get(User,2), change, op, Provider(), preserved_events=retained)
        assert result.status == ('needs_reconciliation' if extra else 'applied')
        if not extra:
            audit = db.scalar(select(Audit).where(Audit.change_id==change.id, Audit.event=='handover_verified'))
            assert {e['id'] for e in audit.details['retained_external_events']} == {77,78}
        else:
            assert not db.scalar(select(WeeklySchedule).where(WeeklySchedule.change_id==change.id))
