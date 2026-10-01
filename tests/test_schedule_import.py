import pytest
from sqlalchemy import select,func
from app.roster import import_schedules
from app.models import User,Location,Scope
from app.config import settings
from app.wiw import WIWError,WIW
from test_portal import sign_in

class Schedules:
    def __init__(self,rows): self.rows=rows
    def call(self,method,path):
        assert (method,path)==('GET','/2/locations')
        return {'locations':self.rows}

def row(i,name,**kw):
    return dict(id=i,name=name,account_id=settings().wiw_account_id,is_deleted=False,**kw)

def test_schedule_choices_repeatable_and_preserves_permissions(db,monkeypatch):
    monkeypatch.setattr(settings(),'wiw_mode','live')
    api=Schedules([row(1,'North'),row(2,'South'),{**row(3,'Old'),'is_deleted':True}])
    assert import_schedules(db,db.get(User,2),api)==2
    db.commit()
    assert import_schedules(db,db.get(User,2),api)==0
    assert set(db.scalars(select(Location.name)))=={'North','South'}
    assert db.get(User,1).location=='North'
    assert db.scalar(select(func.count()).select_from(Scope))==2

def test_schedule_account_validation_atomic(db,monkeypatch):
    monkeypatch.setattr(settings(),'wiw_mode','live')
    with pytest.raises(WIWError):
        import_schedules(db,db.get(User,2),Schedules([row(1,'North'),{**row(2,'Other'),'account_id':-9}]))
    assert db.scalar(select(func.count()).select_from(Location))==0

def test_admin_import_renders_schedules(client,db,monkeypatch):
    db.get(User,2).role='admin';db.commit()
    csrf=sign_in(client,'manager@test.local')
    monkeypatch.setattr(settings(),'wiw_mode','live')
    calls=[]
    def call(self,method,path,**kw):
        calls.append(path)
        return {'locations':[row(1,'New Schedule')]}
    monkeypatch.setattr(WIW,'call',call)
    response=client.post('/admin/import-wiw',data={'csrf':csrf,'schedules_only':'on'})
    assert response.status_code==200
    assert '1 new schedule choices added' in response.text
    assert '<option value="New Schedule"' in response.text
    assert 'value="New Schedule"' in response.text
    assert calls==['/2/locations']
