"""Repair an unchanged, partially created weekly schedule at a DST anchor."""
from datetime import date
from sqlalchemy import select
from .config import settings
from .models import User, Change, Audit, ManagedEvent
from .weekly import local_today, timeline, split_dst_anchor
from .workflow import allowed, audit, canonical
from .weekly_workflow import dispatch_weekly
from .locations import assigned_locations
from .wiw import WIW


def repair_dst_anchor(db, actor, change_id, provider):
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
    approvals = [e for e in entries if e.event in ('approved', 'recovery_plan')]
    if len(approvals) != 1 or approvals[0].event != 'approved':
        raise ValueError('An original approval with no prior replacement repair is required.')
    approval = approvals[0]
    plan = approval.details
    ops = plan['operations']
    if (plan['pre_write_state']['availabilityevents'] or plan.get('managed_event_snapshots')
            or plan.get('retained_external_events') or len(ops) < 2
            or any(op['action'] != 'create' for op in ops)
            or plan.get('weekly_schedule') != change.proposed):
        raise ValueError('Recovery requires an unchanged, initially empty, create-only approved plan.')
    journal = [e for e in entries if e.id > approval.id
               and e.event in ('operation_started', 'operation_succeeded', 'write_uncertain')]
    if len(journal) < 4 or len(journal) % 2:
        raise ValueError('Incomplete operation journal. No writes performed.')
    for n in range(0, len(journal), 2):
        start, finish = journal[n:n+2]
        index = 0 if n == 0 else 1
        if (start.event != 'operation_started' or start.details.get('index') != index
                or start.details.get('operation') != ops[index] or finish.details.get('index') != index):
            raise ValueError('Unexpected operation journal. No writes performed.')
        if n == 0:
            if finish.event != 'operation_succeeded':
                raise ValueError('First creation was not verified.')
        elif (finish.event != 'write_uncertain'
              or 'WIW returned HTTP 409 (WIW code 4090).' not in finish.details.get('reason', '')):
            raise ValueError('Recovery only accepts an explicit conflict after the first creation.')
    saved = journal[1].details.get('response', {}).get('availabilityevent')
    if not isinstance(saved, dict) or type(saved.get('id')) is not int:
        raise ValueError('Missing saved creation.')
    first_parts = split_dst_anchor(ops[0]['payload'])
    if len(first_parts) != 2 or first_parts[0].get('recurrence'):
        raise ValueError('The first creation is not a splittable clock-change anchor.')
    current = provider.get(saved['id'], change.wiw_user_id)
    if current != saved:
        raise ValueError('The saved WIW event changed. No writes performed.')
    WIW.verify_payload(current, ops[0]['payload'])
    observed = provider.read(change.wiw_user_id, change.read_start, change.read_end)
    if canonical(observed) != canonical({'availabilityevents': [current]}):
        raise ValueError('Other WIW availability changed. No writes performed.')
    managed = db.scalars(select(ManagedEvent).where(ManagedEvent.employee_id == employee.id,
                                                  ManagedEvent.active.is_(True))).all()
    if (len(managed) != 1 or managed[0].event_id != saved['id'] or managed[0].snapshot != saved
            or change.before['timeline_ids'] or timeline(db, employee.id, False)):
        raise ValueError('The portal schedule changed. No writes performed.')
    final_events = [p for op in ops for p in split_dst_anchor(op['payload'])]
    # Explicit null clears recurrence on the existing record; the first Sunday
    # remains in place. The approved infinite series resumes the following week.
    operations = [{'action': 'update', 'event_id': saved['id'],
                   'payload': {**first_parts[0], 'recurrence': None}, 'rfc_dates': True}]
    operations += [{'action': 'create', 'payload': p, 'rfc_dates': True} for p in final_events[1:]]
    audit(db, change, actor, 'recovery_plan', {'repair': 'dst_anchor', 'approval_id': approval.id,
        'pre_write_state': observed, 'managed_event_snapshots': [current],
        'retained_external_events': [], 'operations': operations,
        'final_managed_payloads': final_events, 'weekly_schedule': change.proposed})
    change.status = 'applying'
    db.commit()
    return dispatch_weekly(db, actor, change, operations, provider, verify_complete=True,
                           final_events=final_events)
