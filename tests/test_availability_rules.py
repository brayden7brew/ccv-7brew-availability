from datetime import timedelta
import pytest
from fastapi import HTTPException
from app.availability_rules import counted_minutes,employee_rules,validate_employee_rules
from app.models import User,Change
from app.weekly import WeeklyInput,local_today
from test_portal import sign_in


def data(days=14, hours='all_day'):
    return WeeklyInput(effective_date=local_today()+timedelta(days=days),days=[{'mode':hours}]*7)


def test_count_only_operating_window():
    assert counted_minutes([{'mode':'all_day'}]*7)==70*60
    assert counted_minutes([{'mode':'hours','start':'03:00','end':'06:00'}])==60
    assert counted_minutes([{'mode':'hours','start':'22:00','end':'00:00'}])==60
    assert counted_minutes([{'mode':'hours','start':'00:00','end':'04:59'}])==0
    assert counted_minutes([{'mode':'hours','start':'05:00','end':'20:00'}])==600


def test_default_notice_includes_new_hires_and_legacy_exception_flags(db):
    person=db.get(User,1)
    assert person.minimum_available_minutes==900 and person.notice_days==14
    person.first_request_notice_exception=True
    assert employee_rules(db,person)['earliest']==local_today()+timedelta(days=14)
    with pytest.raises(HTTPException):validate_employee_rules(db,person,data(13))
    validate_employee_rules(db,person,data(14))
    person.notice_enabled=False
    validate_employee_rules(db,person,data(1))


def test_minimum_threshold_and_disable(db):
    person=db.get(User,1)
    proposal=data()
    day_type=type(proposal.days[0])
    proposal.days=[day_type(mode='hours',start='05:00',end='23:00')]+[day_type(mode='none')]*6
    with pytest.raises(HTTPException,match='10 hours per day'):
        validate_employee_rules(db,person,proposal)
    proposal.days[0]=day_type(mode='all_day')
    with pytest.raises(HTTPException):validate_employee_rules(db,person,proposal)
    proposal.days[1]=day_type(mode='hours',start='05:00',end='10:00')
    snapshot=validate_employee_rules(db,person,proposal)
    assert snapshot['counted_minutes']==900 and snapshot['daily_count_cap_minutes']==600
    proposal.days[1].end='09:59'
    with pytest.raises(HTTPException):validate_employee_rules(db,person,proposal)
    person.minimum_hours_enabled=False
    validate_employee_rules(db,person,data(hours='none'))


def test_first_submission_requires_full_notice(client,db):
    person=db.get(User,1)
    person.first_request_notice_exception=True
    db.commit()
    csrf=sign_in(client)
    form={'csrf':csrf,'action':'weekly','effective_date':(local_today()+timedelta(days=1)).isoformat()}
    form.update({f'day_{i}_mode':'all_day' for i in range(7)})
    assert client.post('/requests',data=form).status_code==422
    assert db.query(Change).count()==0
    form['effective_date']=(local_today()+timedelta(days=13)).isoformat()
    assert client.post('/requests',data=form).status_code==422
    form['effective_date']=(local_today()+timedelta(days=14)).isoformat()
    assert client.post('/requests',data=form).status_code==200
    assert db.query(Change).count()==1


def test_admin_can_set_and_disable_rules(client,db):
    csrf=sign_in(client)
    assert client.post('/admin/users/1/rules',data={'csrf':csrf}).status_code==403
    db.get(User,2).role='admin';db.commit();csrf=sign_in(client,'manager@test.local')
    form={'csrf':csrf,'minimum_hours':'20.5','notice_days':'21','minimum_hours_enabled':'on','notice_enabled':'on'}
    assert client.post('/admin/users/1/rules',data=form).status_code==200
    db.refresh(db.get(User,1));person=db.get(User,1)
    assert person.minimum_available_minutes==1230 and person.notice_days==21
    del form['minimum_hours_enabled'];del form['notice_enabled']
    assert client.post('/admin/users/1/rules',data=form).status_code==200
    assert not person.minimum_hours_enabled and not person.notice_enabled
    form['minimum_hours']='NaN'
    assert client.post('/admin/users/1/rules',data=form).status_code==422


def test_one_long_day_is_rejected_before_submission(client,db):
    csrf=sign_in(client)
    form={'csrf':csrf,'action':'weekly','effective_date':(local_today()+timedelta(days=14)).isoformat()}
    form.update({f'day_{i}_mode':'none' for i in range(7)})
    form.update(day_0_mode='hours',day_0_start='05:00',day_0_end='23:00')
    assert client.post('/requests',data=form).status_code==422
    assert db.query(Change).count()==0
    form.update(day_1_mode='hours',day_1_start='05:00',day_1_end='10:00')
    assert client.post('/requests',data=form).status_code==200
    change=db.query(Change).one()
    assert change.proposed['days'][0]['end']=='23:00'
