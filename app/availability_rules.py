"""Employee-specific rules, counted in local weekly wall-clock minutes."""
from datetime import timedelta
from sqlalchemy import select
from fastapi import HTTPException
from .models import Change
from .weekly import local_today, minutes, end_minutes


def counted_minutes(days):
    total=0
    for day in days:
        if day['mode']=='all_day': total+=10*60
        elif day['mode']=='hours':
            total+=min(10*60,max(0,min(end_minutes(day['end']),23*60)-max(minutes(day['start']),5*60)))
    return total


def employee_rules(db,user):
    first=bool(user.first_request_notice_exception and not db.scalar(select(Change.id).where(Change.employee_id==user.id,Change.action=='weekly').limit(1)))
    notice=max(1,user.notice_days) if user.notice_enabled and not first else 1
    return dict(minimum_enabled=user.minimum_hours_enabled,minimum_minutes=user.minimum_available_minutes,
        minimum_hours=user.minimum_available_minutes/60,daily_count_cap_minutes=600,notice_enabled=user.notice_enabled,notice_days=user.notice_days,
        first_request_exception=first and user.notice_enabled,earliest=local_today()+timedelta(days=notice))


def validate_employee_rules(db,user,data):
    rules=employee_rules(db,user)
    counted=counted_minutes(data.model_dump(mode='json')['days'])
    if data.effective_date<rules['earliest']:
        raise HTTPException(422,f"Your availability must start on or after {rules['earliest'].isoformat()} ({rules['notice_days']} days’ notice).")
    if rules['minimum_enabled'] and counted<rules['minimum_minutes']:
        raise HTTPException(422,f"Provide at least {rules['minimum_hours']:g} available hours per week between 5 a.m. and 11 p.m. At most 10 hours per day count toward this minimum. Your request counts as {counted/60:g} hours.")
    return {**rules,'earliest':rules['earliest'].isoformat(),'counted_minutes':counted}
