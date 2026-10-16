"""Recover an initial approval stopped at its first legacy-series update.

Verify every pre-cutoff occurrence before replanning; never replay the old COUNT.
"""
from datetime import date, datetime, time
from zoneinfo import ZoneInfo
from sqlalchemy import select
from .config import settings
from .models import User, Change, Audit, ManagedEvent
from .weekly import local_today, timeline, event_plan
from .workflow import allowed, audit, canonical
from .weekly_workflow import dispatch_weekly
from .locations import assigned_locations
from .legacy_availability import handover
from .event_identity import finite_occurrences


def repair_split_update(db, actor, change_id, provider):
    existing = db.get(Change, change_id)
    if not existing:
        raise ValueError('Request not found.')
    employee = db.scalar(select(User).where(User.id == existing.employee_id).with_for_update())
    change = db.scalar(select(Change).where(Change.id == change_id).with_for_update()
                       .execution_options(populate_existing=True))
    cfg = settings()
    if (not actor.active or actor.role not in ('manager', 'admin') or actor.id == employee.id
            or (actor.role != 'admin' and not allowed(db, actor, change))):
        raise ValueError('An authorized manager or administrator must recover this request.')
    if (change.action != 'weekly' or change.status != 'needs_reconciliation'
            or change.manager_id is None or change.dry_run is not False
            or cfg.dry_run or cfg.wiw_mode != 'live'):
        raise ValueError('Recovery requires an unresolved approved live weekly request.')
    effective = date.fromisoformat(change.proposed['effective_date'])
    if (effective <= local_today() or not employee.active or employee.wiw_user_id != change.wiw_user_id
            or set(assigned_locations(employee)) != set(assigned_locations(change))):
        raise ValueError('Employee mapping or start date changed.')
    if (timeline(db, employee.id, False) or change.before.get('timeline_ids')
            or db.scalar(select(ManagedEvent.id).where(ManagedEvent.employee_id == employee.id,
                                                       ManagedEvent.active.is_(True)))):
        raise ValueError('Recovery is limited to an initial schedule without managed events.')
    if db.scalar(select(Change.id).where(Change.employee_id == employee.id,
            Change.id != change.id, Change.status.in_(['applying', 'needs_reconciliation']))):
        raise ValueError('Another unresolved approval exists for this employee.')
    entries = db.scalars(select(Audit).where(Audit.change_id == change.id).order_by(Audit.id)).all()
    approvals = [e for e in entries if e.event in ('approved', 'recovery_plan', 'recovery_verified')]
    if len(approvals) != 1 or approvals[0].event != 'approved':
        raise ValueError('An original approval with no prior recovery attempt is required.')
    approval = approvals[0]
    journal = [e for e in entries if e.id > approval.id and
               e.event in ('operation_started', 'operation_succeeded', 'write_uncertain')]
    ops = approval.details['operations']
    if (len(journal) != 2 or journal[0].event != 'operation_started'
            or journal[1].event != 'write_uncertain'
            or any(e.details.get('index') != 0 for e in journal)
            or not ops or ops[0]['action'] != 'update'
            or journal[0].details.get('operation') != ops[0]):
        raise ValueError('Only a stopped first update can be recovered here.')
    original = approval.details['pre_write_state']['availabilityevents']
    current = provider.read(change.wiw_user_id, change.read_start, change.read_end)
    events = current['availabilityevents']
    # This narrow recovery handles one legacy record split into exactly two.
    if (len(original) != 1 or original[0]['id'] != ops[0]['event_id'] or len(events) != 2
            or len({e['id'] for e in events}) != 2 or original[0]['id'] not in {e['id'] for e in events}):
        raise ValueError('Expected the original series and exactly one split record.')
    for event in events:
        if provider.get(event['id'], change.wiw_user_id) != event:
            raise ValueError('WIW changed during inspection.')
    cutoff = datetime.combine(effective, time(), ZoneInfo(cfg.business_timezone))
    if finite_occurrences(original, before=cutoff) != finite_occurrences(events, before=cutoff):
        raise ValueError('Availability before the requested date changed. No writes performed.')
    edits, retained = handover(events, effective)
    operations = edits + [{'action':'create', 'payload':p, 'rfc_dates':True}
                          for p in event_plan([change.proposed])]
    for operation in operations:
        if operation['action'] == 'update':
            operation['rfc_dates'] = True
    if canonical(provider.read(change.wiw_user_id, change.read_start, change.read_end)) != canonical(current):
        raise ValueError('WIW changed during inspection. No writes performed.')
    audit(db, change, actor, 'recovery_plan', {'repair':'split_update', 'approval_id':approval.id,
        'pre_write_state':current, 'managed_event_snapshots':[], 'retained_external_events':retained,
        'operations':operations, 'weekly_schedule':change.proposed, 'preserve_before':effective.isoformat()})
    change.status = 'applying'
    db.commit()
    return dispatch_weekly(db, actor, change, operations, provider, preserved_events=retained)
