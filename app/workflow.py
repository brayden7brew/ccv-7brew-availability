import json
from fastapi import HTTPException
from sqlalchemy import select
from .models import User, Scope, Change, Audit
from .wiw import WIW
from .locations import assigned_locations

class RequestAlreadyDecided(HTTPException):
    def __init__(self):
        super().__init__(409, 'This request has already been decided.')


def audit(db, change, actor, event, details):
    db.add(Audit(change_id=change.id, actor_id=actor.id if actor else None, event=event, details=details))
    if event in ('submitted','approved','rejected'):
        from .notifications import enqueue_notifications
        enqueue_notifications(db, change, event)

def allowed(db, user, change):
    if user.id == change.employee_id:
        return True
    return user.role in ('manager', 'admin') and db.scalar(select(Scope.id).where(
        Scope.manager_id == user.id, Scope.location == change.location)) is not None

def canonical(state):
    return json.dumps(sorted(state['availabilityevents'], key=lambda x: x['id']), sort_keys=True)

def decide(db, actor, change_id, decision, note, provider=None, replace_existing=False, *, enqueue=False, run_queued=False):
    # Employee row serializes decisions across all of this employee's requests in Postgres.
    existing = db.get(Change, change_id)
    if not existing or not allowed(db, actor, existing):
        raise HTTPException(404, 'Request not found.')
    if not actor.active or actor.role not in ('manager', 'admin'):
        raise HTTPException(403, 'An authorized manager must decide this request.')
    if actor.id == existing.employee_id and decision != 'approve':
        raise HTTPException(403, 'You cannot reject your own availability.')
    employee = db.scalar(select(User).where(User.id == existing.employee_id).with_for_update())
    change = db.scalar(select(Change).where(Change.id == change_id).with_for_update().execution_options(populate_existing=True))
    if change.status != ('queued' if run_queued else 'pending'):
        raise RequestAlreadyDecided()
    if decision not in ('approve', 'reject') or len(note) > 2000 or (decision == 'reject' and not note.strip()):
        raise HTTPException(422, 'Choose approve/reject and include a reason when rejecting.')
    if employee.location != change.location or not employee.active or employee.wiw_user_id != change.wiw_user_id or set(assigned_locations(employee)) != set(assigned_locations(change)):
        raise HTTPException(409, 'Employee mapping changed. Submit a new request.')
    if decision == 'reject':
        change.status, change.manager_id, change.manager_note = 'rejected', actor.id, note
        audit(db, change, actor, 'rejected', {'note': note})
        db.commit()
        return change
    if change.action != 'weekly':
        raise HTTPException(409, 'Please resubmit this older request using the weekly availability form.')
    unresolved = db.scalar(select(Change.id).where(Change.employee_id == employee.id,
        Change.id != change.id, Change.status.in_(['queued', 'preparing', 'applying', 'needs_reconciliation'])))
    if unresolved:
        raise HTTPException(409, 'An earlier write needs reconciliation before another approval.')
    from .config import settings
    if enqueue:
        change.status, change.manager_id, change.manager_note = 'queued', actor.id, note
        change.dry_run = settings().dry_run
        audit(db, change, actor, 'approval_queued', {'note':note, 'dry_run':change.dry_run})
        db.commit()
        return change
    if run_queued:
        if (change.manager_id != actor.id or change.dry_run != settings().dry_run
                or (not change.dry_run and settings().wiw_mode != 'live')):
            raise HTTPException(409, 'Approval owner or server mode changed while queued.')
        # Commit the claim before remote calls. A crashed worker must never replay writes.
        change.status = 'preparing'
        audit(db, change, actor, 'approval_started', {})
        db.commit()
    provider = provider or WIW()
    from .weekly_workflow import approve_weekly
    return approve_weekly(db, actor, change, note, provider, replace_existing=replace_existing)
