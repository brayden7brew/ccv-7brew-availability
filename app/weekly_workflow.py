"""Durable, journaled multi-event approval. Never retries ambiguous WIW writes."""
from datetime import date
from fastapi import HTTPException
from sqlalchemy import select
from .models import ManagedEvent, WeeklySchedule
from .weekly import timeline, timeline_ids, display_schedule, event_plan, local_today
from .config import settings
from .wiw import WIWError

def approve_weekly(db, actor, change, note, provider, replace_existing=False):
    from .workflow import audit, canonical
    dry_run = settings().dry_run
    rows = timeline(db, change.employee_id, dry_run)
    current = provider.read(change.wiw_user_id, change.read_start, change.read_end)
    managed = db.scalars(select(ManagedEvent).where(ManagedEvent.employee_id == change.employee_id,
                                                    ManagedEvent.active.is_(True))).all()
    fresh = []
    for item in managed:
        event = provider.get(item.event_id, change.wiw_user_id)
        fresh.append(event)
        if event != item.snapshot:
            change.status = 'conflict'
            audit(db, change, actor, 'conflict', {'message':'Portal-managed event was changed in WIW.', 'current':event})
            db.commit()
            return change
    if (change.before.get('dry_run') != dry_run or timeline_ids(rows) != change.before['timeline_ids']
            or canonical(current) != canonical(change.before)):
        change.status = 'conflict'
        audit(db, change, actor, 'conflict', {'current':current, 'timeline_ids':timeline_ids(rows)})
        db.commit()
        return change
    known = {m.event_id for m in managed}
    effective = date.fromisoformat(change.proposed['effective_date'])
    from .legacy_availability import handover
    try:
        legacy_operations, retained = handover(
            [e for e in current['availabilityevents'] if e['id'] not in known], effective)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    if legacy_operations and not replace_existing:
        raise HTTPException(409, 'Review the existing WIW availability and confirm replacement from the requested start date before approving.')
    if effective <= local_today():
        raise HTTPException(409, 'The start date has passed. Submit a request with a future start date.')
    profiles = {r.effective_date.isoformat():display_schedule(r) for r in rows}
    profiles[effective.isoformat()] = change.proposed
    try:
        payloads = event_plan(list(profiles.values()))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    operations = (legacy_operations + [{'action':'delete', 'event_id':m.event_id} for m in managed] +
                  [{'action':'create', 'payload':p} for p in payloads])
    change.manager_id, change.manager_note, change.dry_run = actor.id, note, dry_run
    change.status = 'approved_dry_run' if dry_run else 'applying'
    audit(db, change, actor, 'approved', {'note':note,'dry_run':dry_run,'pre_write_state':current,
        'managed_event_snapshots':fresh, 'retained_external_events':retained, 'replace_existing':bool(legacy_operations), 'operations':operations, 'weekly_schedule':change.proposed})
    db.commit()
    return dispatch_weekly(db, actor, change, operations, provider)


def dispatch_weekly(db, actor, change, operations, provider, start_index=0):
    from .workflow import audit
    managed = db.scalars(select(ManagedEvent).where(ManagedEvent.employee_id == change.employee_id,
        ManagedEvent.active.is_(True))).all()
    if not change.dry_run:
        for index, operation in enumerate(operations[start_index:], start=start_index):
            audit(db, change, actor, 'operation_started', {'index':index, 'operation':operation})
            db.commit()
            try:
                result = provider.weekly_operation(change, operation)
            except WIWError as exc:
                change.status = 'needs_reconciliation'
                audit(db, change, actor, 'write_uncertain', {'index':index, 'reason':str(exc),
                    'message':'Weekly change may be partially applied. Do not retry. Inspect the saved operation journal and WIW.'})
                db.commit()
                return change
            if operation['action'] == 'delete':
                item = next((m for m in managed if m.event_id == operation['event_id']), None)
                if item: item.active = False
            elif operation['action'] == 'create':
                event = result['availabilityevent']
                db.add(ManagedEvent(employee_id=change.employee_id, event_id=event['id'], snapshot=event))
            audit(db, change, actor, 'operation_succeeded', {'index':index, 'response':result})
            db.commit()
        change.status = 'applied'
        audit(db, change, actor, 'applied', {'operation_count':len(operations)})
    db.add(WeeklySchedule(change_id=change.id, employee_id=change.employee_id,
        effective_date=date.fromisoformat(change.proposed['effective_date']), days=change.proposed['days'], dry_run=change.dry_run))
    db.commit()
    return change

def reconcile_weekly(db, actor, change, outcome, note, provider):
    """Verify the complete final plan before unlocking a partially delivered week.

    This performs only reads. The operator restores/completes WIW manually first;
    missing or extra events leave the request unresolved. Never infers completion
    from a partial journal and never repeats an external write.
    """
    from .models import Audit
    from .workflow import audit, canonical
    from dateutil.parser import parse
    approval = db.scalars(select(Audit).where(Audit.change_id == change.id,
        Audit.event.in_(['approved','recovery_plan'])).order_by(Audit.id.desc())).first()
    if not approval:
        raise ValueError('No saved approved operation plan found.')
    current = provider.read(change.wiw_user_id, change.read_start, change.read_end)
    if outcome == 'not-applied':
        if canonical(current) != canonical(approval.details['pre_write_state']):
            raise ValueError('WIW does not exactly match the saved pre-write state. Restore or complete the intended schedule before reconciling.')
        snapshots = approval.details['managed_event_snapshots']
    else:
        retained = approval.details.get('retained_external_events', [])
        expected = retained + [op['payload'] for op in approval.details['operations'] if op['action']=='create']
        from .event_identity import event_signature as fingerprint
        from collections import Counter
        if Counter(map(fingerprint,current['availabilityevents'])) != Counter(map(fingerprint,expected)):
            raise ValueError('WIW does not match the complete approved weekly plan. Leave this request unresolved until every planned event is verified.')
        retained_ids = {event['id'] for event in retained}
        snapshots = [event for event in current['availabilityevents'] if event['id'] not in retained_ids]
        if not db.scalar(select(WeeklySchedule.id).where(WeeklySchedule.change_id==change.id)):
            db.add(WeeklySchedule(change_id=change.id, employee_id=change.employee_id,
                effective_date=date.fromisoformat(change.proposed['effective_date']),
                days=change.proposed['days'], dry_run=False))
    existing = db.scalars(select(ManagedEvent).where(ManagedEvent.employee_id==change.employee_id)).all()
    by_id={e.event_id:e for e in existing}
    for e in existing: e.active=False
    for snapshot in snapshots:
        if snapshot['id'] in by_id:
            by_id[snapshot['id']].active=True
            by_id[snapshot['id']].snapshot=snapshot
        else:
            db.add(ManagedEvent(employee_id=change.employee_id,event_id=snapshot['id'],snapshot=snapshot))
    change.status='reconciled_applied' if outcome=='applied' else 'reconciled_not_applied'
    audit(db,change,actor,change.status,{'note':note,'observed_state':current})


def resume_verified_deletions(db, actor, change_id, provider):
    """Continue only an approved plan whose attempted prefix consists of absent deletes."""
    from .models import Change, User, Audit
    from .workflow import allowed, audit, canonical
    existing = db.get(Change, change_id)
    if not existing:
        raise ValueError('Request not found.')
    employee = db.scalar(select(User).where(User.id == existing.employee_id).with_for_update())
    change = db.scalar(select(Change).where(Change.id == change_id).with_for_update().execution_options(populate_existing=True))
    if (not actor.active or actor.role not in ('manager', 'admin') or actor.id == employee.id
            or (actor.role != 'admin' and not allowed(db, actor, change))):
        raise ValueError('An authorized manager or administrator must recover this request.')
    if (change.action != 'weekly' or change.status != 'needs_reconciliation' or change.dry_run
            or settings().dry_run or settings().wiw_mode != 'live'):
        raise ValueError('Recovery requires an unresolved, approved live weekly request.')
    from .locations import assigned_locations
    if (not employee.active or employee.wiw_user_id != change.wiw_user_id
            or set(assigned_locations(employee)) != set(assigned_locations(change))
            or date.fromisoformat(change.proposed['effective_date']) <= local_today()):
        raise ValueError('Employee mapping or start date no longer permits this approval.')
    approval = db.scalars(select(Audit).where(Audit.change_id == change.id,
        Audit.event.in_(['approved','recovery_plan'])).order_by(Audit.id.desc())).first()
    if not approval: raise ValueError('No approved plan found.')
    operations = approval.details['operations']
    starts = db.scalars(select(Audit).where(Audit.change_id == change.id,
        Audit.event == 'operation_started').order_by(Audit.id)).all()
    if not starts: raise ValueError('No attempted deletion to verify.')
    deleted = set()
    for index, entry in enumerate(starts):
        if (index >= len(operations) or entry.details.get('index') != index
                or entry.details.get('operation') != operations[index]
                or operations[index]['action'] != 'delete'):
            raise ValueError('Recovery is limited to a deletion-only prefix. No writes performed.')
        event_id = operations[index]['event_id']
        try:
            provider.get(event_id, change.wiw_user_id)
        except WIWError as exc:
            if exc.reason != 'http_error' or exc.http_status != 404:
                raise ValueError('Could not verify the deleted event is absent.') from exc
        else:
            raise ValueError('An attempted deletion is still present. No writes performed.')
        deleted.add(event_id)
    expected = {'availabilityevents':[e for e in approval.details['pre_write_state']['availabilityevents'] if e['id'] not in deleted]}
    if canonical(provider.read(change.wiw_user_id, change.read_start, change.read_end)) != canonical(expected):
        raise ValueError('Other WIW availability changed. No writes performed.')
    for event in approval.details['managed_event_snapshots']:
        if event['id'] not in deleted and provider.get(event['id'], change.wiw_user_id) != event:
            raise ValueError('Another managed event changed. No writes performed.')
    if timeline_ids(timeline(db, change.employee_id, False)) != change.before['timeline_ids']:
        raise ValueError('The approved portal schedule changed. No writes performed.')
    for item in db.scalars(select(ManagedEvent).where(ManagedEvent.employee_id == employee.id,
            ManagedEvent.event_id.in_(deleted))):
        item.active = False
    audit(db, change, actor, 'recovery_verified', {'deleted_event_ids':sorted(deleted),
        'next_operation':len(starts), 'approval_id':approval.id})
    change.status = 'applying'
    db.commit()
    return dispatch_weekly(db, actor, change, operations, provider, start_index=len(starts))
