import copy
from datetime import timedelta
import pytest
from sqlalchemy import select
from app.config import settings
from app.models import Audit, Change, User, WeeklySchedule, now
from app.weekly import local_today
from app.replacement_recovery import recover_requested_schedule
from app.wiw import WIW, WIWError
from test_portal import submit


@pytest.mark.parametrize('case',['complete','create_mismatch','readback_mismatch','state_changed','recent','wrong_date'])
def test_replacement_preserves_earlier_entries_and_verifies_before_delete(client,db,monkeypatch,case):
    monkeypatch.setattr(settings(),'dry_run',False)
    monkeypatch.setattr(settings(),'wiw_mode','live')
    start=local_today()+timedelta(days=30)
    early=local_today()+timedelta(days=1)
    def entry(key,day): return {'id':key,'user_id':1,'account_id':10,'type':1,'all_day':False,
        'start_time':f'{day}T09:00:00-04:00','end_time':f'{day}T17:00:00-04:00','recurrence':''}
    before={11:entry(11,early),12:entry(12,start)}
    class Provider:
        verify_payload=staticmethod(WIW.verify_payload)
        def __init__(self): self.events=copy.deepcopy(before); self.calls=[]; self.counter=100; self.reads=0
        def read(self,*args):
            self.reads+=1
            if case=='state_changed' and self.reads==3: self.events[13]=entry(13,early)
            return {'availabilityevents':copy.deepcopy(list(self.events.values()))}
        def get(self,key,*args):
            if key not in self.events: raise WIWError('Missing',reason='http_error',http_status=404)
            event=copy.deepcopy(self.events[key])
            if case=='readback_mismatch' and key>100: event['recurrence']=''
            return event
        def weekly_operation(self,change,op):
            self.calls.append(copy.deepcopy(op))
            if op['action']=='create':
                if case=='create_mismatch': raise WIWError('Incorrect returned times')
                self.counter+=1
                event={'id':self.counter,'user_id':1,'account_id':10,**op['payload']}
                self.events[self.counter]=event
                return {'availabilityevent':copy.deepcopy(event)}
            del self.events[op['event_id']]
            return {'success':True}
    provider=Provider()
    monkeypatch.setattr('app.main.WIW',lambda:provider)
    submit(client)
    c=db.get(Change,1)
    c.status='applying'; c.manager_id=2; c.dry_run=False
    old=now()-timedelta(minutes=30)
    for audit in db.scalars(select(Audit)): audit.created=old
    db.add(Audit(change_id=1,actor_id=2,event='approved',created=now() if case=='recent' else old,details={}))
    db.commit()
    provider.reads=0
    if case in ('recent','wrong_date'):
        with pytest.raises(ValueError): recover_requested_schedule(db,db.get(User,2),1,
            (start+timedelta(days=1)).isoformat() if case=='wrong_date' else c.proposed['effective_date'],provider)
        assert provider.calls==[]
        return
    result=recover_requested_schedule(db,db.get(User,2),1,c.proposed['effective_date'],provider)
    assert provider.events[11]==before[11]
    if case=='complete':
        assert result.status=='applied'
        assert [op['action'] for op in provider.calls]==['create','create','delete']
        assert 12 not in provider.events
        assert db.scalar(select(WeeklySchedule.change_id))==1
    else:
        assert result.status=='needs_reconciliation'
        assert not any(op['action']=='delete' for op in provider.calls)
        assert provider.events[12]==before[12]
    count=len(provider.calls)
    with pytest.raises(ValueError): recover_requested_schedule(db,db.get(User,2),1,c.proposed['effective_date'],provider)
    assert len(provider.calls)==count
