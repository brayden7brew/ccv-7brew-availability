from datetime import datetime,time,timedelta
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from app.models import User,Change,Audit,Blackout,TimeOffSettings,EmailOutbox
from app.config import settings
from app.weekly import local_today
from app.time_off import process_time_off,capacity
from app.wiw import WIWError
from test_portal import sign_in

@pytest.fixture
def pilot(db):
    db.add(TimeOffSettings(id=1,enabled=True,everyone=False))
    db.get(User,1).time_off_access=True;db.get(User,2).time_off_access=True
    db.commit()


def data(csrf,days=20,**extra):
    return dict(csrf=csrf,first=(local_today()+timedelta(days=days)).isoformat(),full_day='on',reason='Appointment',**extra)


def request(client,db,**extra):
    token=sign_in(client)
    response=client.post('/time-off',data=data(token,**extra),follow_redirects=False)
    assert response.status_code==303,response.text
    return db.scalar(select(Change).where(Change.action=='time_off'))


def blackout(db,maximum=1,period='morning'):
    row=Blackout(location='North',day=local_today()+timedelta(days=20),period=period,
                 maximum=maximum,explanation='Holiday staffing',evening_end=23)
    db.add(row);db.commit();return row

class API:
    def __init__(self): self.writes=[];self.events={};self.external=[]
    def read(self,uid,*args): return {'availabilityevents':self.events.get(uid,[])}
    def call(self,method,path,**kwargs):
        if path=='/2/requesttypes':
            return {'request-types':[{'id':7,'name':'Hourly Employee Time Off','allow_unpaid':True}]}
        if path=='/2/requests' and method=='GET': return {'requests':self.external,'more':False}
        if path=='/2/requests' and method=='POST':
            self.writes.append(kwargs['payload']);return {'request':{'id':99,**kwargs['payload']}}
        if path=='/2/requests/99': return {'request':{'id':99,**self.writes[-1]}}
        raise AssertionError((method,path))


def test_feature_off_even_for_admin_and_individual_grants(client,db):
    db.get(User,2).role='admin';db.commit();csrf=sign_in(client,'manager@test.local')
    assert client.get('/time-off').status_code==403
    db.add(TimeOffSettings(id=1,enabled=True,everyone=False));db.commit()
    assert client.get('/time-off').status_code==403
    db.get(User,2).time_off_access=True;db.commit()
    assert client.get('/time-off').status_code==200
    response=client.post('/admin/time-off',data={'csrf':csrf,'enabled':'on','everyone':'on'})
    assert response.status_code==200
    sign_in(client)
    assert client.get('/time-off').status_code==200
    assert 'href="/time-off"' in client.get('/').text
    db.get(TimeOffSettings,1).enabled=False;db.commit()
    assert client.get('/time-off').status_code==403


@pytest.mark.parametrize('days,reason,status',[(13,'Appointment',422),(14,'',422),(14,'Appointment',303)])
def test_notice_reason_and_full_days(client,db,pilot,days,reason,status):
    csrf=sign_in(client);payload=data(csrf,days);payload['reason']=reason
    assert client.post('/time-off',data=payload,follow_redirects=False).status_code==status


def test_partial_day_validation_and_optional_decline_reason(client,db,pilot):
    csrf=sign_in(client);payload=data(csrf);payload.pop('full_day');payload.update({'from':'09:00','to':'08:00'})
    assert client.post('/time-off',data=payload).status_code==422
    payload['to']='12:00'
    assert client.post('/time-off',data=payload).status_code==200
    change=db.scalar(select(Change))
    assert not change.proposed['full_day']
    csrf=sign_in(client,'manager@test.local')
    assert client.post(f'/time-off/{change.id}/decision',data={'csrf':csrf,'decision':'reject'}).status_code==200
    assert db.get(Change,change.id).status=='rejected'


def test_scoping_duplicates_and_no_generic_decision_bypass(client,db,pilot):
    change=request(client,db)
    csrf=sign_in(client)
    assert client.post('/time-off',data=data(csrf)).status_code==409
    assert client.post(f'/time-off/{change.id}/decision',data={'csrf':csrf,'decision':'approve'}).status_code==403
    db.get(User,3).time_off_access=True;db.commit();csrf=sign_in(client,'other@test.local')
    assert client.get(f'/time-off/{change.id}').status_code==404
    assert client.post(f'/time-off/{change.id}/decision',data={'csrf':csrf,'decision':'approve'}).status_code==403
    csrf=sign_in(client,'manager@test.local')
    assert client.post(f'/requests/{change.id}/decision',data={'csrf':csrf,'decision':'reject','note':'test'}).status_code==404


def test_blackout_blocks_submission_and_counts_normal_availability(client,db,pilot,monkeypatch):
    api=API();row=blackout(db,1)
    zone=ZoneInfo(settings().business_timezone);a=datetime.combine(row.day,time(5),zone)
    api.events[2]=[{'type':1,'start_time':a.isoformat(),'end_time':a.replace(hour=6).isoformat(),'all_day':False}]
    monkeypatch.setattr('app.time_off.WIW',lambda:api)
    csrf=sign_in(client)
    response=client.post('/time-off',data=data(csrf))
    assert response.status_code==409 and 'Holiday staffing' in response.text
    payload=data(csrf);payload.pop('full_day');payload.update({'from':'12:00','to':'17:00'})
    assert client.post('/time-off',data=payload).status_code==200
    assert '1 unavailable / 1 allowed' in client.get(f'/time-off?check_date={row.day}').text


def test_blackout_permission_and_zero_capacity(client,db,pilot):
    csrf=sign_in(client,'manager@test.local')
    payload={'csrf':csrf,'location':'South','day':(local_today()+timedelta(days=20)).isoformat(),
             'period':'evening','maximum':'0','explanation':'High volume','evening_end':'22'}
    assert client.post('/time-off/blackouts',data=payload).status_code==403
    payload['location']='North'
    assert client.post('/time-off/blackouts',data=payload).status_code==200
    assert db.scalar(select(Blackout)).evening_end==22
    csrf=sign_in(client)
    assert client.post('/time-off',data=data(csrf)).status_code==409


@pytest.mark.parametrize('failure',[None,'post','verify','readback'])
def test_durable_wiw_creation_and_no_replay(client,db,pilot,monkeypatch,failure):
    monkeypatch.setattr(settings(),'dry_run',False);monkeypatch.setattr(settings(),'wiw_mode','live')
    change=request(client,db);csrf=sign_in(client,'manager@test.local')
    assert client.post(f'/time-off/{change.id}/decision',data={'csrf':csrf,'decision':'approve'}).status_code==200
    assert change.status=='queued'
    class Broken(API):
        def call(self,method,path,**kwargs):
            if method=='POST' and failure=='post': raise WIWError('Uncertain write')
            result=super().call(method,path,**kwargs)
            if method=='POST' and failure=='verify': result['request']['user_id']=999
            if path=='/2/requests/99' and failure=='readback': raise WIWError('Read unavailable')
            return result
    api=Broken();factory=sessionmaker(db.bind,expire_on_commit=False)
    assert process_time_off(factory,lambda:api)==1
    db.expire_all();assert db.get(Change,change.id).status==('applied' if failure is None else 'needs_reconciliation')
    assert process_time_off(factory,lambda:api)==0
    assert len(api.writes)<=1
    if failure is None:
        assert api.writes[0]['paid'] is False and api.writes[0]['user_status']==2
        assert api.writes[0]['end_time'].split('T')[1].startswith('23:59:59')


def test_capacity_reservation_stops_second_approval(client,db,pilot,monkeypatch):
    first=request(client,db)
    # Second person at the same primary stand, not a secondary-stand employee.
    db.get(User,3).location='North';db.get(User,3).time_off_access=True;db.commit()
    csrf=sign_in(client,'other@test.local');assert client.post('/time-off',data=data(csrf)).status_code==200
    second=db.scalar(select(Change).where(Change.employee_id==3))
    blackout(db,1);monkeypatch.setattr('app.time_off.WIW',API)
    csrf=sign_in(client,'manager@test.local')
    assert client.post(f'/time-off/{first.id}/decision',data={'csrf':csrf,'decision':'approve'}).status_code==200
    assert client.post(f'/time-off/{second.id}/decision',data={'csrf':csrf,'decision':'approve'}).status_code==409
    assert db.get(Change,second.id).status=='pending'


def test_feature_off_pauses_queued_work(client,db,pilot):
    change=request(client,db);csrf=sign_in(client,'manager@test.local')
    client.post(f'/time-off/{change.id}/decision',data={'csrf':csrf,'decision':'approve'})
    db.get(TimeOffSettings,1).enabled=False;db.commit()
    assert process_time_off(sessionmaker(db.bind),API)==0
    assert change.status=='queued'


def test_distinct_people_primary_stand_and_period_boundaries(client,db,pilot,monkeypatch):
    from app.time_off import blocked_people
    from app.time_off_wiw import read_requests
    monkeypatch.setattr(settings(),'wiw_mode','live')
    row=blackout(db,2);api=API()
    day=datetime.combine(row.day,time(),ZoneInfo(settings().business_timezone))
    # Same employee unavailable and approved off counts once. Secondary stand does not count.
    db.get(User,3).secondary_location='North';db.commit()
    api.events[1]=[{'type':1,'start_time':day.replace(hour=5).isoformat(),
        'end_time':day.replace(hour=6).isoformat()}]
    api.external=[{'id':id,'account_id':settings().wiw_account_id,'user_id':uid,'user_status':2,
        'start_time':day.replace(hour=5).isoformat(),'end_time':day.replace(hour=12).isoformat()}
        for id,uid in [(41,1),(42,3)]]
    people,_,_=blocked_people(db,api,row,read_requests(api,row.day,row.day),{})
    assert people=={1}
    row.period='afternoon'
    assert blocked_people(db,api,row,api.external,{})[0]==set()
    row.period='evening';row.evening_end=22
    api.events[1]=[{'type':1,'start_time':day.replace(hour=22).isoformat(),
        'end_time':day.replace(hour=23).isoformat()}]
    assert blocked_people(db,api,row,[],{})[0]==set()
    row.evening_end=23
    assert blocked_people(db,api,row,[],{})[0]=={1}


def test_dry_run_has_no_write_and_time_off_does_not_use_availability_quota(client,db,pilot):
    from app.request_limits import request_usage
    change=request(client,db);csrf=sign_in(client,'manager@test.local')
    assert request_usage(db,1)['count']==0
    client.post(f'/time-off/{change.id}/decision',data={'csrf':csrf,'decision':'approve'})
    api=API();process_time_off(sessionmaker(db.bind),lambda:api)
    db.expire_all()
    assert db.get(Change,change.id).status=='approved_dry_run'
    assert not api.writes


def test_multi_day_display_and_dst_duration():
    from app.time_off import parse_form,interval
    from app.presentation import time_off_label
    from datetime import timezone
    # Dates are generated for the next fall transition, always >=14 days away.
    year=local_today().year+1
    from datetime import date
    day=date(year,11,1)
    day+=timedelta(days=(6-day.weekday())%7)
    proposed,_=parse_form({'first':str(day),'full_day':'on','reason':'Trip'})
    a,b=interval(proposed)
    assert (b.astimezone(timezone.utc)-a.astimezone(timezone.utc)).total_seconds()==25*3600
    assert 'All day' in time_off_label(proposed)
    assert '12:00' not in time_off_label(proposed)


def test_incomplete_wiw_time_off_fails_closed(monkeypatch):
    from app.time_off_wiw import read_requests
    monkeypatch.setattr(settings(),'wiw_mode','live')
    class Repeated(API):
        def call(self,*args,**kwargs):
            return {'requests':[{'id':1,'user_id':1,'account_id':settings().wiw_account_id}], 'more':True}
    with pytest.raises(WIWError,match='pagination'):
        read_requests(Repeated(),local_today(),local_today())


def test_two_postgres_workers_do_not_repeat_time_off(client,db,pilot,monkeypatch):
    if db.bind.dialect.name!='postgresql': pytest.skip('Requires PostgreSQL row locking')
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    monkeypatch.setattr(settings(),'dry_run',False);monkeypatch.setattr(settings(),'wiw_mode','live')
    change=request(client,db);csrf=sign_in(client,'manager@test.local')
    client.post(f'/time-off/{change.id}/decision',data={'csrf':csrf,'decision':'approve'})
    db.commit()
    entered,release=Event(),Event()
    class Slow(API):
        def call(self,method,path,**kwargs):
            if method=='POST':
                entered.set();assert release.wait(10)
            return super().call(method,path,**kwargs)
    api=Slow();factory=sessionmaker(db.bind,expire_on_commit=False)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(process_time_off,factory,lambda:api)
        assert entered.wait(10)
        try: assert pool.submit(process_time_off,factory,lambda:api).result(5)==0
        finally: release.set()
        assert first.result(10)==1
    assert len(api.writes)==1


def test_unrelated_period_does_not_require_wiw_capacity_read(client,db,pilot,monkeypatch):
    blackout(db,0,period='morning')
    class Unreachable(API):
        def read(self,*args): raise AssertionError('Unrelated period must not read WIW')
        def call(self,*args,**kwargs): raise AssertionError('Unrelated period must not read WIW')
    monkeypatch.setattr('app.time_off.WIW',Unreachable)
    monkeypatch.setattr(settings(),'wiw_mode','live')
    csrf=sign_in(client);payload=data(csrf);payload.pop('full_day')
    payload.update({'from':'12:00','to':'17:00'})
    assert client.post('/time-off',data=payload).status_code==200
