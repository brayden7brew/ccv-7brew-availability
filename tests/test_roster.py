import pytest
from sqlalchemy import select, func
from app.models import User, AdminAudit
from app.config import settings
from app.roster import import_roster
from app.wiw import WIWError, WIW
from test_portal import sign_in


def record(uid=44, **overrides):
    return dict(id=uid, account_id=settings().wiw_account_id, activated=True,
                is_deleted=False, first_name='Test', last_name='Employee',
                email='test@example.com', **overrides)

class Roster:
    def __init__(self, rows): self.rows=rows
    def call(self, method, path, **kw):
        assert (method,path)==('GET','/2/users')
        assert kw['params']=={'show_pending':'false','show_deleted':'false'}
        return {'users':self.rows}


def test_import_preserves_existing_and_is_repeatable(db,monkeypatch):
    monkeypatch.setattr(settings(),'wiw_mode','live')
    db.get(User,1).active=False
    rows=[record(1),record(),{**record(45),'activated':False},{**record(46),'is_deleted':True}]
    assert import_roster(db,db.get(User,2),Roster(rows))==1
    db.commit()
    assert import_roster(db,db.get(User,2),Roster(rows))==0
    person=db.scalar(select(User).where(User.wiw_user_id==44))
    assert person.role=='employee' and person.password_hash=='!'
    assert person.notification_email=='test@example.com'
    assert db.get(User,1).active is False
    assert db.get(User,2).role=='manager'
    assert db.scalar(select(func.count()).select_from(AdminAudit))==1


def test_wrong_account_fails_before_any_insert(db,monkeypatch):
    monkeypatch.setattr(settings(),'wiw_mode','live')
    with pytest.raises(WIWError):
        import_roster(db,db.get(User,2),Roster([record(),{**record(45),'account_id':-1}]))
    assert db.scalar(select(func.count()).select_from(User))==3


def test_import_route_admin_csrf_and_success(client,db,monkeypatch):
    token=sign_in(client)
    assert client.post('/admin/import-wiw',data={'csrf':token}).status_code==403
    db.get(User,2).role='admin';db.commit()
    token=sign_in(client,'manager@test.local')
    assert client.post('/admin/import-wiw',data={}).status_code==403
    monkeypatch.setattr(settings(),'wiw_mode','live')
    monkeypatch.setattr(WIW,'call',lambda *a,**kw:{'users':[record()], 'locations':[]})
    response=client.post('/admin/import-wiw',data={'csrf':token})
    assert response.status_code==200
    assert '1 new employees added' in response.text
