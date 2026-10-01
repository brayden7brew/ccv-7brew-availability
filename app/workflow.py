import json
from fastapi import HTTPException
from sqlalchemy import select
from .models import User, Scope, Change, Audit
from .wiw import WIW
from .locations import assigned_locations

def audit(db, change, actor, event, details):
    db.add(Audit(change_id=change.id, actor_id=actor.id if actor else None, event=event, details=details))
    if event in ('submitted','approved','rejected'):
        from .notifications import enqueue_notifications
        enqueue_notifications(db, change, event)

def allowed(db, user, change):
    if user.id == change.employee_id:
        return True
    return user.role in ('manager', 'admin') and db.scalar(select(Scope.id).where(
        Scope.manager_id == user.id, Scope.location.in_(assigned_locations(change)))) is not None

def canonical(state):
    return json.dumps(sorted(state['availabilityevents'], key=lambda x: x['id']), sort_keys=True)

def decide(db, actor, change_id, decision, note, provider=None, replace_existing=False):
    provider = provider or WIW()
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
    if change.status != 'pending':
        raise HTTPException(409, 'This request has already been decided.')
    if decision not in ('approve', 'reject') or len(note) > 2000 or (decision == 'reject' and not note.strip()):
        raise HTTPException(422, 'Choose approve/reject and include a reason when rejecting.')
    if not employee.active or employee.wiw_user_id != change.wiw_user_id or set(assigned_locations(employee)) != set(assigned_locations(change)):
        raise HTTPException(409, 'Employee mapping changed. Submit a new request.')
    if decision == 'reject':
        change.status, change.manager_id, change.manager_note = 'rejected', actor.id, note
        audit(db, change, actor, 'rejected', {'note': note})
        db.commit()
        return change
    if change.action != 'weekly':
        raise HTTPException(409, 'Please resubmit this older request using the weekly availability form.')
    unresolved = db.scalar(select(Change.id).where(Change.employee_id == employee.id,
        Change.status.in_(['applying', 'needs_reconciliation'])))
    if unresolved:
        raise HTTPException(409, 'An earlier write needs reconciliation before another approval.')
    from .weekly_workflow import approve_weekly
    return approve_weekly(db, actor, change, note, provider, replace_existing=replace_existing)
