import copy
from datetime import date
import pytest
from sqlalchemy import select
from app.config import settings
from app.models import User, Change, Audit, ManagedEvent, WeeklySchedule
from app.weekly import event_plan
from app.dst_recovery import repair_dst_anchor
from app.wiw import WIWError
from app.weekly_workflow import reconcile_weekly


@pytest.mark.parametrize('case', ['success', 'edited', 'extra', 'timeout', 'unauthorized', 'partial_update', 'bad_readback', 'prior_repair'])
def test_dst_repair_preserves_first_day_and_verifies_complete_week(db, monkeypatch, case):
    monkeypatch.setattr(settings(), 'dry_run', False)
    monkeypatch.setattr(settings(), 'wiw_mode', 'live')
    monkeypatch.setattr('app.dst_recovery.local_today', lambda: date(2026, 10, 8))
    proposed = {'effective_date': '2026-10-26', 'days': [{'mode':'none'} for _ in range(7)]}
    new_plan = event_plan([proposed], today=date(2026, 10, 8))
    old_plan = [{**new_plan[0], 'recurrence': 'FREQ=WEEKLY;BYDAY=SU'}] + new_plan[2:]
    ops = [{'action':'create', 'payload': p, 'rfc_dates':True} for p in old_plan]
    saved = {**old_plan[0], 'id':77, 'account_id':10, 'user_id':1}
    change = Change(employee_id=1, wiw_user_id=1, location='North', action='weekly',
                    status='needs_reconciliation', manager_id=2, dry_run=False,
                    proposed=proposed, before={'timeline_ids':[]}, read_start='2026-10-26', read_end='2027-02-15')
    db.add(change); db.flush()
    db.add(ManagedEvent(employee_id=1, event_id=77, snapshot=copy.deepcopy(saved)))
    journal = [('approved', {'pre_write_state':{'availabilityevents':[]}, 'operations':ops,
                            'weekly_schedule':proposed, 'managed_event_snapshots':[], 'retained_external_events':[]}),
               ('operation_started', {'index':0,'operation':ops[0]}),
               ('operation_succeeded', {'index':0,'response':{'availabilityevent':saved}})]
    for _ in range(2):
        journal += [('operation_started', {'index':1,'operation':ops[1]}),
                    ('write_uncertain', {'index':1,'reason':'Timeout' if case=='timeout' else 'WIW returned HTTP 409 (WIW code 4090). Conflict'})]
    if case=='prior_repair': journal += [('recovery_plan', {})]
    for event, details in journal: db.add(Audit(change_id=change.id,actor_id=2,event=event,details=details))
    db.commit()
    class Provider:
        def __init__(self): self.events={77:copy.deepcopy(saved)}; self.calls=[]
        def get(self, i, *args): return copy.deepcopy(self.events[i])
        def read(self, *args):
            return {'availabilityevents':[] if self.calls and case=='bad_readback' else copy.deepcopy(list(self.events.values()))}
        def weekly_operation(self, c, op):
            self.calls.append(copy.deepcopy(op))
            if case=='partial_update': raise WIWError('Uncertain update')
            if op['action']=='update':
                self.events[op['event_id']].update(op['payload'])
                return {'availabilityevents':[copy.deepcopy(self.events[op['event_id']])]}
            assert op['action']=='create'
            i=100+len(self.calls)
            event={**op['payload'],'id':i,'user_id':1,'account_id':10}
            self.events[i]=event
            return {'availabilityevent':copy.deepcopy(event)}
    provider=Provider()
    if case=='edited': provider.events[77]['notes']='External edit'
    if case=='extra': provider.events[78]={**saved,'id':78}
    actor=db.get(User,3 if case=='unauthorized' else 2)
    if case in ('edited','extra','timeout','unauthorized','prior_repair'):
        with pytest.raises(ValueError): repair_dst_anchor(db,actor,change.id,provider)
        assert not provider.calls
    else:
        result=repair_dst_anchor(db,actor,change.id,provider)
        assert result.status==('applied' if case=='success' else 'needs_reconciliation')
        assert provider.calls[0]['action']=='update' and provider.calls[0]['event_id']==77
        assert provider.calls[0]['payload']['recurrence'] is None
        assert all(op['action']!='delete' for op in provider.calls)
        if case=='success':
            assert len(provider.events)==8 and len(provider.calls)==8
            assert db.scalar(select(ManagedEvent).where(ManagedEvent.event_id==77)).snapshot['recurrence'] is None
            reconcile_weekly(db,actor,result,'applied','Verified recovered schedule',provider)
            assert len(db.scalars(select(ManagedEvent).where(ManagedEvent.active.is_(True))).all())==8
            assert len(db.scalars(select(WeeklySchedule)).all())==1
        else: assert not db.scalars(select(WeeklySchedule)).all()
        with pytest.raises(ValueError): repair_dst_anchor(db,actor,change.id,provider)
