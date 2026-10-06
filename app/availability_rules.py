"""Employee-specific rules, counted in local weekly wall-clock minutes."""
from datetime import timedelta
from fastapi import HTTPException
from .weekly import local_today, blocked


def counted_minutes(days):
    total = 0
    for day in days:
        unavailable = sum(max(0, min(end, 1380) - max(start, 300))
                          for start, end in blocked(day))
        total += min(600, 1080 - unavailable)
    return total


def employee_rules(db,user):
    notice=max(1,user.notice_days) if user.notice_enabled else 1
    return dict(minimum_enabled=user.minimum_hours_enabled,minimum_minutes=user.minimum_available_minutes,
        minimum_hours=user.minimum_available_minutes/60,daily_count_cap_minutes=600,notice_enabled=user.notice_enabled,notice_days=user.notice_days,
        earliest=local_today()+timedelta(days=notice))


def validate_employee_rules(db,user,data):
    rules=employee_rules(db,user)
    counted=counted_minutes(data.model_dump(mode='json')['days'])
    if data.effective_date<rules['earliest']:
        raise HTTPException(422,f"Your availability must start on or after {rules['earliest'].isoformat()} ({rules['notice_days']} days’ notice).")
    if rules['minimum_enabled'] and counted<rules['minimum_minutes']:
        raise HTTPException(422,f"Provide at least {rules['minimum_hours']:g} available hours per week between 5 a.m. and 11 p.m. At most 10 hours per day count toward this minimum. Your request counts as {counted/60:g} hours. If you need to provide fewer hours, contact your stand manager to discuss an exception.")
    return {**rules,'earliest':rules['earliest'].isoformat(),'counted_minutes':counted}
