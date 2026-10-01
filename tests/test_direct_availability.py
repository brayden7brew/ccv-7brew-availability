from datetime import timedelta
import pytest
from sqlalchemy import select
from app.config import settings
from app.models import User, Change, Audit, EmailOutbox, now
from app.workflow import decide
from app.wiw import WIWError
from fastapi import HTTPException
from test_portal import sign_in, token

@pytest.mark.parametrize('role,dry,fail', [('manager',True,False),('manager',False,False),('admin',False,False),('manager',False,True),('employee',False,False)])
def test_own_submission(role,dry,fail,client,db,monkeypatch):
    user=db.get(User,1); user.role=role; db.commit()
    monkeypatch.setattr(settings(),'dry_run',dry)
    monkeypatch.setattr(settings(),'email_enabled',True)
    class Provider:
        writes=0
        def read(self,*args): return {'availabilityevents':[]}
        def weekly_operation(self,change,op):
            assert change.manager_id==change.employee_id==1
            self.writes+=1
            if fail: raise WIWError('Unavailable')
            return {'availabilityevent':{'id':100+self.writes,'user_id':1,'account_id':10,**op['payload']}}
    provider=Provider(); monkeypatch.setattr('app.main.WIW',lambda:provider)
    sign_in(client)
    form=client.get('/requests/new')
    assert ('Save my availability' in form.text)==(role!='employee')
    data={'csrf':token(form),'action':'weekly','effective_date':(now()+timedelta(days=30)).strftime('%Y-%m-%d')}
    data.update({f'day_{i}_mode':'all_day' for i in range(7)})
    data.update(day_0_mode='hours',day_0_start='09:00',day_0_end='17:00')
    response=client.post('/requests',data=data)
    assert response.status_code==200
    change=db.scalars(select(Change)).one()
    expected='pending' if role=='employee' else 'approved_dry_run' if dry else 'needs_reconciliation' if fail else 'applied'
    assert change.status==expected
    if role=='employee':
        assert provider.writes==0
        with pytest.raises(HTTPException): decide(db,user,change.id,'approve','',provider)
    else:
        assert change.manager_id==1
        assert not db.scalar(select(EmailOutbox.id).where(EmailOutbox.event=='submitted'))
        assert db.scalar(select(Audit.id).where(Audit.event=='submitted_direct'))
        if fail:
            response=client.post('/requests',data=data)
            assert response.status_code==409
            assert len(db.scalars(select(Change)).all())==1
            assert provider.writes==1
