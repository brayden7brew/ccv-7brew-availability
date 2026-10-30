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
    employee_page=client.get(f'/time-off?check_date={row.day}').text
    assert 'Check date' not in employee_page and '<h2>Blackout dates</h2>' not in employee_page


def test_blackout_permission_and_zero_capacity(client,db,pilot):
    csrf=sign_in(client,'manager@test.local')
    payload={'csrf':csrf,'location':'South','day':(local_today()+timedelta(days=20)).isoformat(),
             'period':'evening','maximum':'0','explanation':'High volume','evening_end':'22'}
    assert client.post('/time-off/blackouts',data=payload).status_code==403
    payload['location']='North'
    assert client.post('/time-off/blackouts',data=payload).status_code==200
    assert db.scalar(select(Blackout)).evening_end==(23 if (local_today()+timedelta(days=20)).weekday() in (4,5) else 22)
    csrf=sign_in(client)
    assert client.post('/time-off',data=data(csrf)).status_code==409


@pytest.mark.parametrize('failure',[None,'post','verify','readback','wrong_id'])
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
            if path=='/2/requests/99' and failure=='wrong_id': result['request']['id']=100
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
    expected={1} if row.day.weekday() in (4,5) else set()
    assert blocked_people(db,api,row,[],{})[0]==expected
    row.evening_end=23  # Older stored overrides cannot change the weekday rule.
    assert blocked_people(db,api,row,[],{})[0]==expected


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


def test_blackout_never_blocks_normal_availability_changes(client,db,pilot):
    from test_portal import submit,Fake
    from app.workflow import decide
    day=local_today()+timedelta(days=30)
    for period in ('morning','afternoon','evening'):
        db.add(Blackout(location='North',day=day,period=period,maximum=0,
                       explanation='Full capacity',evening_end=23))
    db.commit()
    submit(client)
    change=db.scalar(select(Change).where(Change.action=='weekly'))
    decide(db,db.get(User,2),change.id,'approve','',Fake())
    assert change.status=='approved_dry_run'


def test_notification_button_uses_saved_request_not_comment(client,db,pilot,monkeypatch):
    from app.mailer import process_batch
    monkeypatch.setattr(settings(),'email_enabled',True)
    db.get(User,1).notification_email='employee@example.com'
    db.get(User,2).notification_email='manager@example.com';db.commit()
    change=request(client,db)
    item=db.scalar(select(EmailOutbox))
    item.body='View request: https://example.invalid/untrusted\n'+item.body;db.commit()
    links=[]
    assert process_batch(sessionmaker(db.bind),lambda message: links.append(message.link))==1
    assert links==[settings().public_base_url.rstrip('/')+f'/time-off/{change.id}']


@pytest.mark.parametrize('weekday',range(7))
@pytest.mark.parametrize('period',['all_day','evening'])
def test_blackout_closing_follows_day_of_week(weekday,period):
    from app.time_off import blackout_interval,blackout_label
    from datetime import date
    day=date(2026,11,2)+timedelta(days=weekday)
    row=Blackout(day=day,period=period,evening_end=22)
    start,end=blackout_interval(row)
    expected=23 if weekday in (4,5) else 22
    assert start.hour==(5 if period=='all_day' else 17)
    assert end.hour==expected
    assert f'{expected-12} PM' in blackout_label(row)


def test_blackout_defaults_to_whole_day_and_counts_each_person_once(client,db,pilot,monkeypatch):
    csrf=sign_in(client,'manager@test.local');day=local_today()+timedelta(days=20)
    payload={'csrf':csrf,'location':'North','day':str(day),'maximum':'1','explanation':'Holiday'}
    assert client.post('/time-off/blackouts',data=payload).status_code==200
    row=db.scalar(select(Blackout))
    assert row.period=='all_day'
    api=API();zone=ZoneInfo(settings().business_timezone);start=datetime.combine(day,time(5),zone)
    api.events[2]=[{'type':1,'start_time':start.replace(hour=hour).isoformat(),
                   'end_time':start.replace(hour=hour+1).isoformat()} for hour in (5,17)]
    monkeypatch.setattr('app.time_off.WIW',lambda:api)
    csrf=sign_in(client);partial=data(csrf);partial.pop('full_day')
    partial.update({'from':'12:00','to':'13:00'})
    assert client.post('/time-off',data=partial).status_code==409
    coverage=capacity(db,api,db.get(User,1),{'start':start.isoformat(),'end':start.replace(hour=22).isoformat()})
    assert coverage[0]['count']==1


def test_managers_only_manage_scoped_stands_and_cannot_select_all(client,db,pilot):
    csrf=sign_in(client,'manager@test.local')
    html=client.get('/time-off').text
    assert 'value="North" readonly' in html
    assert 'name="all_stands"' not in html and '<option>South</option>' not in html
    payload={'csrf':csrf,'day':str(local_today()+timedelta(days=20)),'maximum':'2','explanation':'Holiday'}
    assert client.post('/time-off/blackouts',data={**payload,'all_stands':'on'}).status_code==403
    assert client.post('/time-off/blackouts',data={**payload,'location':'South'}).status_code==403
    assert db.scalar(select(Blackout)) is None


def test_admin_can_apply_blackout_to_specific_or_all_stands(client,db,pilot):
    from app.models import Location
    db.get(User,2).role='admin'
    db.add(Location(name='West'));db.commit()
    csrf=sign_in(client,'manager@test.local')
    html=client.get('/time-off').text
    assert 'name="all_stands"' in html and 'value="West"' in html
    payload={'csrf':csrf,'day':str(local_today()+timedelta(days=20)),'maximum':'2','explanation':'Holiday',
             'locations':['South','West']}
    assert client.post('/time-off/blackouts',data=payload).status_code==200
    assert set(db.scalars(select(Blackout.location)))=={'South','West'}
    assert client.post('/time-off/blackouts',data={**payload,'locations':['West','Unknown']}).status_code==403
    assert set(db.scalars(select(Blackout.location)))=={'South','West'}
    payload['all_stands']='on';payload['maximum']='3'
    assert client.post('/time-off/blackouts',data=payload).status_code==200
    rows=db.scalars(select(Blackout)).all()
    assert {row.location for row in rows}=={'North','South','West'}
    assert all(row.maximum==3 for row in rows)


def test_blackout_list_counts_requests_approved_and_caps_by_stand_and_period(client,db,pilot):
    from app.time_off import blackout_request_counts
    morning=blackout(db,3,'morning');evening=blackout(db,2,'evening')
    start=datetime.combine(morning.day,time(8),ZoneInfo(settings().business_timezone))
    def add(uid,status,start,end):
        change=Change(employee_id=uid,location=db.get(User,uid).location,wiw_user_id=uid,action='time_off',
                      proposed={'start':start.isoformat(),'end':end.isoformat(),'full_day':False},
                      status=status,before={},read_start=start.isoformat(),read_end=end.isoformat())
        db.add(change);db.flush();return change
    add(1,'pending',start,start.replace(hour=9))
    add(1,'queued',start,start.replace(hour=10))
    add(1,'rejected',start,start.replace(hour=11))
    add(1,'applied',start.replace(hour=17),start.replace(hour=18))
    add(3,'applied',start,start.replace(hour=9))  # Other primary stand.
    archived=add(1,'applied',start,start.replace(hour=9))
    db.add(Audit(change_id=archived.id,actor_id=2,event='employee_archived',details={}))
    db.commit()
    counts=blackout_request_counts(db,[morning,evening])
    assert counts[morning.id]=={'requests':3,'approved':1}
    assert counts[evening.id]=={'requests':1,'approved':1}
    sign_in(client,'manager@test.local')
    html=client.get('/time-off').text
    assert '<dt>Requests</dt><dd>3</dd>' in html
    assert '<dt>Approved</dt><dd>1</dd>' in html
    assert '<dt>Cap</dt><dd>3</dd>' in html
    assert 'Check date' not in html
    sign_in(client)
    html=client.get('/time-off?check_date=invalid').text
    assert '<h2>Blackout dates</h2>' not in html
    assert '<dt>Requests</dt>' not in html
