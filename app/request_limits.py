"""Rolling submission counts, independent of decision status and calendar month."""
from datetime import timedelta, timezone
from sqlalchemy import select
from .models import Change, now
from .config import settings

def request_usage(db, employee_id, at=None):
    at = at or now()
    dates = list(db.scalars(select(Change.created).where(
        Change.employee_id == employee_id, Change.action == 'weekly',
        Change.created > at - timedelta(days=30), Change.created <= at
    ).order_by(Change.created)))
    limit = settings().availability_request_limit_30_days
    blocked = bool(limit and len(dates) >= limit)
    reset = dates[len(dates)-limit] + timedelta(days=30) if blocked else None
    if reset and reset.tzinfo is None:
        reset = reset.replace(tzinfo=timezone.utc)
    return dict(count=len(dates), limit=limit, blocked=blocked,
                remaining=max(0, limit-len(dates)) if limit else None, reset=reset)
