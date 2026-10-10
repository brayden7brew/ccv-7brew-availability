"""Calendar-month submission counts, independent of decision status."""
from datetime import timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select
from .models import Change, User, now
from .config import settings

def request_usage(db, employee_id, at=None):
    at = at or now()
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    local = at.astimezone(ZoneInfo(settings().business_timezone))
    month_start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month = (month_start.replace(year=month_start.year + 1, month=1)
                  if month_start.month == 12 else month_start.replace(month=month_start.month + 1))
    month_start = month_start.astimezone(timezone.utc)
    records = list(db.execute(select(Change.created, Change.request_limit_exempt).where(
        Change.employee_id == employee_id, Change.action == 'weekly',
        Change.created >= month_start, Change.created <= at.astimezone(timezone.utc)
    ).order_by(Change.created)))
    dates = [created for created, exempt in records if not exempt]
    person = db.get(User, employee_id)
    credits = person.extra_request_credits if person else 0
    limit = settings().availability_request_limit_30_days
    needs_credit = bool(limit and len(dates) >= limit)
    blocked = needs_credit and credits == 0
    reset = next_month.astimezone(timezone.utc) if blocked else None
    return dict(count=len(records), limit=limit, blocked=blocked, credits=credits, use_credit=needs_credit and credits > 0,
                remaining=max(0, limit-len(dates)) + credits if limit else None, reset=reset)
