"""Durable approvals consumed by the existing background worker.

Only queued requests are claimed. Processing/applying requests are never replayed
on restart: interrupted or ambiguous WIW writes require inspection.
"""
from fastapi import HTTPException
from sqlalchemy import select
from .db import SessionLocal
from .models import Change, User
from .workflow import decide, audit, RequestAlreadyDecided
from .wiw import WIW, WIWError


def process_approval(factory=SessionLocal, api_factory=WIW):
    with factory() as db:
        candidates = db.execute(select(Change.id, Change.employee_id).where(
            Change.status == 'queued').order_by(Change.id).limit(20)).all()
        for change_id, employee_id in candidates:
            # Keep the same employee -> change lock order as submission/decisions.
            employee = db.scalar(select(User).where(User.id == employee_id)
                                 .with_for_update(skip_locked=True))
            if employee is None:
                db.rollback()
                continue
            change = db.scalar(select(Change).where(Change.id == change_id, Change.status == 'queued')
                               .with_for_update(skip_locked=True).execution_options(populate_existing=True))
            if change is None:
                db.rollback()
                continue
            actor = db.get(User, change.manager_id)
            try:
                if actor is None:
                    raise HTTPException(409, 'The approving manager is no longer available.')
                decide(db, actor, change.id, 'approve', change.manager_note, api_factory(), run_queued=True)
            except RequestAlreadyDecided:
                db.rollback()
                continue
            except Exception as exc:
                db.rollback()
                # Reload committed state. Never retry a potentially successful remote write.
                current = db.scalar(select(Change).where(Change.id == change_id).with_for_update()
                                    .execution_options(populate_existing=True))
                if current.status in ('queued', 'preparing', 'applying', 'approved_dry_run'):
                    uncertain = current.status == 'applying'
                    current.status = 'needs_reconciliation' if uncertain else 'approval_failed'
                    message = (str(exc) if isinstance(exc, WIWError) else
                               str(exc.detail) if isinstance(exc, HTTPException) else
                               'Background processing stopped. An administrator must inspect this request.')
                    audit(db, current, actor, 'write_uncertain' if uncertain else 'approval_failed',
                          {'message':message, 'error_type':type(exc).__name__, 'background':True})
                    db.commit()
            return 1
    return 0
