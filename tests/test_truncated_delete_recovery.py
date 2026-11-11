from copy import deepcopy
from datetime import date
import pytest
from app.truncated_delete_recovery import replacement_plan
from app.event_identity import finite_occurrences


def state():
    def event(key,day,start,end,rule,full=False):
        return dict(id=key,type=1,start_time=f'2026-11-{day:02d}T{start}:00-05:00',
            end_time=f'2026-11-{day+1 if full else day:02d}T{end}:00-05:00',
            all_day=full,recurrence=f'FREQ=WEEKLY;BYDAY={rule}')
    original=[event(683,8,'00:00','00:00','SU',True),event(684,9,'00:00','00:00','MO',True),
              event(688,3,'05:00','17:30','TU'),event(689,4,'05:00','14:00','WE'),
              event(692,5,'05:00','12:00','TH'),event(696,6,'05:00','14:00','FR'),
              event(703,7,'05:00','11:00','SA'),event(707,7,'20:00','23:00','SA')]
    current=deepcopy(original[2:]); current[0]['recurrence']='FREQ=WEEKLY;UNTIL=20261105T045959Z;BYDAY=TU'
    future=[dict(type=1,start_time='2026-11-22T12:00:00-05:00',end_time='2026-11-22T23:00:00-05:00',all_day=False,recurrence='FREQ=WEEKLY;BYDAY=SU')]
    return original,current,future


def plan(original,current,future):
    return replacement_plan(original,current,{683,684},688,date(2026,11,22),future,date(2026,11,11))


def test_restore_missing_upcoming_days_without_redeleting_history():
    old,current,future=state()
    ops,retained,count,past=plan(old,current,future)
    assert count==3 and len(past)==3
    assert [p['payload']['start_time'][:10] for p in ops[:3]]==['2026-11-15','2026-11-16','2026-11-17']
    assert sum(op['action']=='update' for op in ops)==5
    assert not any(op['action']=='delete' for op in ops)
    assert ops[-1]['payload']==future[0]
    from datetime import datetime
    from zoneinfo import ZoneInfo
    cutoff=datetime(2026,11,22,tzinfo=ZoneInfo('America/New_York'))
    wanted=finite_occurrences(old,before=cutoff)
    restored=finite_occurrences(retained+[op['payload'] for op in ops[:count]])
    from collections import Counter
    assert restored+Counter(past)==wanted
    assert current==state()[1], 'Planning must not mutate WIW snapshots'


@pytest.mark.parametrize('mutation',['extra','hours','unattempted','extended','missing'])
def test_refuses_unexplained_changes(mutation):
    old,current,future=state()
    if mutation=='extra': current.append({**current[-1],'id':999})
    if mutation=='hours': current[0]['end_time']='2026-11-03T18:00:00-05:00'
    if mutation=='unattempted': current[1]['notes']='changed elsewhere'
    if mutation=='extended': current[0]['recurrence']='FREQ=WEEKLY;BYDAY=TU;UNTIL=20261201T045959Z'
    if mutation=='missing': current.pop()
    with pytest.raises(ValueError): plan(old,current,future)


def test_preview_then_apply_journals_and_verifies_complete_repair(db,monkeypatch):
    from datetime import datetime, timezone
    from sqlalchemy import select
    from app.models import User,Change,Audit,ManagedEvent,WeeklySchedule
    from app.config import settings
    from app.weekly import event_plan
    from app.truncated_delete_recovery import repair_truncated_delete
    from app.wiw import WIWError
    monkeypatch.setattr(settings(),'dry_run',False)
    monkeypatch.setattr(settings(),'wiw_mode','live')
    monkeypatch.setattr('app.truncated_delete_recovery.local_today',lambda:date(2026,11,11))
    monkeypatch.setattr('app.weekly.local_today',lambda:date(2026,11,11))
    old,current,future=state()
    old_days=[dict(mode='none'),dict(mode='none'),dict(mode='unavailable',start='05:00',end='17:30'),
              dict(mode='unavailable',start='05:00',end='14:00'),dict(mode='unavailable',start='05:00',end='12:00'),
              dict(mode='unavailable',start='05:00',end='14:00'),
              dict(mode='unavailable',ranges=[dict(start='05:00',end='11:00'),dict(start='20:00',end='23:00')])]
    old_profile=dict(effective_date='2026-11-03',days=old_days)
    proposed=dict(effective_date='2026-11-22',days=[dict(mode='unavailable',start='12:00',end='23:00')]+[dict(mode='all_day')]*6)
    previous=Change(employee_id=1,location='North',wiw_user_id=1,action='weekly',proposed=old_profile,
        before={},read_start='',read_end='',status='applied',dry_run=False)
    db.add(previous);db.flush()
    schedule=WeeklySchedule(employee_id=1,change_id=previous.id,effective_date=date(2026,11,3),days=old_days,dry_run=False)
    db.add(schedule);db.flush()
    change=Change(employee_id=1,location='North',secondary_location='',wiw_user_id=1,action='weekly',proposed=proposed,
        before={'timeline_ids':[schedule.id]},read_start='2026-11-04T00:00:00-05:00',read_end='2027-11-23T00:00:00-05:00',
        status='needs_reconciliation',manager_id=2,dry_run=False)
    db.add(change);db.flush()
    order=[684,683,688,692,689,696,707,703]
    ops=[{'action':'delete','event_id':key} for key in order]+[
        {'action':'create','payload':p,'rfc_dates':True} for p in event_plan([old_profile,proposed],today=date(2026,11,4))]
    db.add(Audit(change_id=change.id,actor_id=2,event='approved',created=datetime(2026,11,4,17,tzinfo=timezone.utc),
        details={'operations':ops,'pre_write_state':{'availabilityevents':old},'managed_event_snapshots':old,'retained_external_events':[]}))
    db.flush()
    for i in range(3):
        db.add(Audit(change_id=change.id,actor_id=2,event='operation_started',details={'index':i,'operation':ops[i]}));db.flush()
        db.add(Audit(change_id=change.id,actor_id=2,event='write_uncertain' if i==2 else 'operation_succeeded',details={'index':i}));db.flush()
    for event in old: db.add(ManagedEvent(employee_id=1,event_id=event['id'],snapshot=event,active=event['id'] not in (683,684)))
    db.get(User,2).role='admin';db.commit()
    class Provider:
        def __init__(self): self.events={e['id']:deepcopy(e) for e in current};self.writes=[];self.key=1000
        def read(self,*args): return {'availabilityevents':deepcopy(list(self.events.values()))}
        def get(self,key,*args):
            if key not in self.events: raise WIWError('absent',reason='http_error',http_status=404)
            return deepcopy(self.events[key])
        def weekly_operation(self,c,op):
            self.writes.append(deepcopy(op))
            if op['action']=='update':
                self.events[op['event_id']].update(op['payload'])
                return {'availabilityevents':[deepcopy(self.events[op['event_id']])]}
            assert op['action']=='create'
            self.key+=1;self.events[self.key]={'id':self.key,**op['payload']}
            return {'availabilityevent':deepcopy(self.events[self.key])}
    api=Provider();actor=db.get(User,2)
    preview=repair_truncated_delete(db,actor,change.id,api)
    assert preview['restore_occurrences']==3 and preview['past_missing_occurrences_not_recreated']==3
    assert not api.writes and change.status=='needs_reconciliation'
    result=repair_truncated_delete(db,actor,change.id,api,apply=True)
    assert result.status=='applied'
    assert db.scalar(select(WeeklySchedule).where(WeeklySchedule.change_id==change.id))
    assert all(not m.active for m in db.scalars(select(ManagedEvent).where(ManagedEvent.event_id.in_(order))))
    with pytest.raises(ValueError): repair_truncated_delete(db,actor,change.id,api,apply=True)
