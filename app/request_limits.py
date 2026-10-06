"""Rolling submission counts, independent of decision status and calendar month."""
from datetime import timedelta, timezone
from sqlalchemy import select
from .models import Change, User, now
from .config import settings

def request_usage(db, employee_id, at=None):
    at = at or now()
    records = list(db.execute(select(Change.created, Change.request_limit_exempt).where(
        Change.employee_id == employee_id, Change.action == 'weekly',
        Change.created > at - timedelta(days=30), Change.created <= at
    ).order_by(Change.created)))
    dates = [created for created, exempt in records if not exempt]
    person = db.get(User, employee_id)
    credits = person.extra_request_credits if person else 0
    limit = settings().availability_request_limit_30_days
    needs_credit = bool(limit and len(dates) >= limit)
    blocked = needs_credit and credits == 0
    reset = dates[len(dates)-limit] + timedelta(days=30) if blocked else None
    if reset and reset.tzinfo is None:
        reset = reset.replace(tzinfo=timezone.utc)
    return dict(count=len(records), limit=limit, blocked=blocked, credits=credits, use_credit=needs_credit and credits > 0,
                remaining=max(0, limit-len(dates)) + credits if limit else None, reset=reset)
