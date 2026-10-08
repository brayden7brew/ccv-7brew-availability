"""Explicit recovery of a create-only approval stopped by a definite conflict."""
from datetime import date
from sqlalchemy import select
from .config import settings
from .models import User, Change, Audit, ManagedEvent
from .weekly import local_today, timeline, timeline_ids
from .workflow import allowed, audit, canonical
from .weekly_workflow import dispatch_weekly
from .locations import assigned_locations
from .wiw import WIW


def resume_verified_creations(db, actor, change_id, provider):
    existing = db.get(Change, change_id)
    if not existing:
        raise ValueError('Request not found.')
    employee = db.scalar(select(User).where(User.id == existing.employee_id).with_for_update())
    change = db.scalar(select(Change).where(Change.id == change_id).with_for_update()
                       .execution_options(populate_existing=True))
    if (not actor.active or actor.role not in ('manager', 'admin') or actor.id == employee.id
            or (actor.role != 'admin' and not allowed(db, actor, change))):
        raise ValueError('An authorized manager or administrator must recover this request.')
    if (change.action != 'weekly' or change.status != 'needs_reconciliation'
            or change.manager_id is None or change.dry_run is not False
            or settings().dry_run or settings().wiw_mode != 'live'):
        raise ValueError('Recovery requires an unresolved, approved live weekly request.')
    if (not employee.active or employee.wiw_user_id != change.wiw_user_id
            or set(assigned_locations(employee)) != set(assigned_locations(change))
            or date.fromisoformat(change.proposed['effective_date']) <= local_today()):
        raise ValueError('Employee mapping or start date no longer permits this approval.')
    entries = db.scalars(select(Audit).where(Audit.change_id == change.id).order_by(Audit.id)).all()
    approvals = [e for e in entries if e.event in ('approved', 'recovery_plan', 'recovery_verified')]
    if len(approvals) != 1 or approvals[0].event != 'approved':
        raise ValueError('Recovery requires an original approval with no previous recovery attempt.')
    approval = approvals[0]
    plan = approval.details
    operations = plan['operations']
    if (plan['pre_write_state']['availabilityevents'] or plan.get('managed_event_snapshots')
            or plan.get('retained_external_events') or not operations
            or any(op['action'] != 'create' for op in operations)):
        raise ValueError('Recovery requires an initially empty, create-only approved plan.')
    journal = [e for e in entries if e.id > approval.id
               and e.event in ('operation_started', 'operation_succeeded', 'write_uncertain')]
    created = []
    next_index = None
    for index in range(len(operations)):
        pair = journal[index * 2:index * 2 + 2]
        if (len(pair) != 2 or pair[0].event != 'operation_started'
                or pair[0].details.get('index') != index
                or pair[0].details.get('operation') != operations[index]
                or pair[1].details.get('index') != index):
            raise ValueError('The operation journal is incomplete or ambiguous. No writes performed.')
        result = pair[1]
        if result.event == 'write_uncertain':
            if ('WIW returned HTTP 409 (WIW code 4090).' not in result.details.get('reason', '')
                    or len(journal) != (index + 1) * 2):
                raise ValueError('Only a definite conflict rejection can be resumed.')
            next_index = index
            break
        if result.event != 'operation_succeeded':
            raise ValueError('Unverified operation. No writes performed.')
        saved = result.details.get('response', {}).get('availabilityevent')
        if not isinstance(saved, dict) or type(saved.get('id')) is not int:
            raise ValueError('Missing saved creation. No writes performed.')
        current = provider.get(saved['id'], change.wiw_user_id)
        if current != saved:
            raise ValueError('A saved creation changed in WIW. No writes performed.')
        WIW.verify_payload(current, operations[index]['payload'])
        created.append(current)
    if next_index is None or not created:
        raise ValueError('No verified creation prefix followed by a conflict.')
    if canonical(provider.read(change.wiw_user_id, change.read_start, change.read_end)) != canonical({'availabilityevents': created}):
        raise ValueError('Other WIW availability changed. No writes performed.')
    managed = db.scalars(select(ManagedEvent).where(ManagedEvent.employee_id == employee.id,
                                                  ManagedEvent.active.is_(True))).all()
    if ({m.event_id: m.snapshot for m in managed} != {e['id']: e for e in created}
            or timeline_ids(timeline(db, employee.id, False)) != change.before['timeline_ids']):
        raise ValueError('The approved portal schedule changed. No writes performed.')
    audit(db, change, actor, 'recovery_verified', {'approval_id': approval.id,
        'created_event_ids': [e['id'] for e in created], 'next_operation': next_index})
    change.status = 'applying'
    db.commit()
    return dispatch_weekly(db, actor, change, operations, provider, start_index=next_index,
                           verify_complete=True)
