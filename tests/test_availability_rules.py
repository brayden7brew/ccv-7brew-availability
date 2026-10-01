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
    assert counted_minutes([{'mode':'all_day'}]*7)==126*60
    assert counted_minutes([{'mode':'hours','start':'03:00','end':'06:00'}])==60
    assert counted_minutes([{'mode':'hours','start':'22:00','end':'00:00'}])==60
    assert counted_minutes([{'mode':'hours','start':'00:00','end':'04:59'}])==0
    assert counted_minutes([{'mode':'hours','start':'05:00','end':'20:00'}])==900


def test_default_notice_and_first_request_exception(db):
    person=db.get(User,1)
    assert person.minimum_available_minutes==900 and person.notice_days==14
    assert employee_rules(db,person)['earliest']==local_today()+timedelta(days=1)
    person.first_request_notice_exception=False
    with pytest.raises(HTTPException):validate_employee_rules(db,person,data(13))
    validate_employee_rules(db,person,data(14))
    person.notice_enabled=False
    validate_employee_rules(db,person,data(1))


def test_minimum_threshold_and_disable(db):
    person=db.get(User,1)
    proposal=data()
    proposal.days=[type(proposal.days[0])(mode='hours',start='05:00',end='20:00')]+[type(proposal.days[0])(mode='none')]*6
    validate_employee_rules(db,person,proposal)
    proposal.days[0].end='19:59'
    with pytest.raises(HTTPException):validate_employee_rules(db,person,proposal)
    person.minimum_hours_enabled=False
    validate_employee_rules(db,person,data(hours='none'))


def test_submission_consumes_exception_only_on_success(client,db):
    csrf=sign_in(client)
    form={'csrf':csrf,'action':'weekly','effective_date':(local_today()+timedelta(days=1)).isoformat()}
    form.update({f'day_{i}_mode':'none' for i in range(7)})
    assert client.post('/requests',data=form).status_code==422
    db.refresh(db.get(User,1))
    assert db.get(User,1).first_request_notice_exception
    form.update({f'day_{i}_mode':'all_day' for i in range(7)})
    assert client.post('/requests',data=form).status_code==200
    assert not db.get(User,1).first_request_notice_exception
    assert client.post('/requests',data=form).status_code==422


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
