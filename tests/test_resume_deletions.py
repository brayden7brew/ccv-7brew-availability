import copy
from datetime import timedelta
import pytest
from sqlalchemy import select
from app.config import settings
from app.models import User, Change, Audit, WeeklySchedule
from app.weekly import local_today
from app.workflow import decide
from app.weekly_workflow import resume_verified_deletions
from app.wiw import WIWError
from test_portal import submit


@pytest.mark.parametrize('case',['resume','changed','still_present','read_error','create_started'])
def test_recovery_only_continues_verified_delete_prefix(client,db,monkeypatch,case):
    monkeypatch.setattr(settings(),'dry_run',False)
    monkeypatch.setattr(settings(),'wiw_mode','live')
    day=(local_today()+timedelta(days=40)).isoformat()
    original={'id':77,'user_id':1,'account_id':10,'type':1,
              'start_time':day+'T05:00:00-04:00','end_time':day+'T10:00:00-04:00'}
    class Provider:
        def __init__(self): self.events={77:copy.deepcopy(original)}; self.calls=[]; self.next=100
        def read(self,*args): return {'availabilityevents':copy.deepcopy(list(self.events.values()))}
        def get(self,key,*args):
            if case=='read_error': raise WIWError('Denied',reason='http_error',http_status=403)
            if key not in self.events: raise WIWError('Missing',reason='http_error',http_status=404)
            return copy.deepcopy(self.events[key])
        def weekly_operation(self,change,op):
            self.calls.append(copy.deepcopy(op))
            if op['action']=='delete':
                self.events.pop(op['event_id'])
                raise WIWError('Unconfirmed delete')
            self.next+=1
            value={'id':self.next,'user_id':1,'account_id':10,**op['payload']}
            self.events[self.next]=value
            return {'availabilityevent':value}
    provider=Provider()
    monkeypatch.setattr('app.main.WIW',lambda:provider)
    submit(client)
    actor=db.get(User,2)
    result=decide(db,actor,1,'approve','',provider,replace_existing=True)
    assert result.status=='needs_reconciliation' and len(provider.calls)==1
    if case=='changed': provider.events[88]={**original,'id':88}
    if case=='still_present': provider.events[77]=original
    if case=='create_started':
        db.add(Audit(change_id=1,actor_id=2,event='operation_started',details={'index':1,'operation':{'action':'create'}})); db.commit()
    if case!='resume':
        with pytest.raises(ValueError): resume_verified_deletions(db,actor,1,provider)
        assert len(provider.calls)==1 and db.get(Change,1).status=='needs_reconciliation'
    else:
        result=resume_verified_deletions(db,actor,1,provider)
        assert result.status=='applied'
        assert [op['action'] for op in provider.calls]==['delete','create','create']
        assert len(db.scalars(select(WeeklySchedule)).all())==1
        assert len(db.scalars(select(Audit).where(Audit.event=='recovery_verified')).all())==1
        with pytest.raises(ValueError): resume_verified_deletions(db,actor,1,provider)
        assert len(provider.calls)==3
