"""Explicit, create-first recovery of a stalled initial weekly approval."""
from collections import Counter
from datetime import date, timedelta, timezone
from sqlalchemy import select
from .config import settings
from .models import User, Change, Audit, ManagedEvent, WeeklySchedule, now
from .workflow import allowed, audit, canonical
from .weekly import event_plan, local_today, timeline
from .legacy_availability import handover
from .event_identity import event_signature
from .locations import assigned_locations
from .wiw import WIWError


def recover_requested_schedule(db, actor, request_id, effective_date, provider):
    initial = db.get(Change, request_id)
    if not initial: raise ValueError('Request not found.')
    employee = db.scalar(select(User).where(User.id == initial.employee_id).with_for_update())
    change = db.scalar(select(Change).where(Change.id == request_id).with_for_update().execution_options(populate_existing=True))
    cfg = settings()
    if (not actor.active or actor.id == employee.id or actor.role not in ('admin','manager')
            or (actor.role != 'admin' and not allowed(db, actor, change))):
        raise ValueError('An authorized manager or administrator is required.')
    if (change.status not in ('applying','needs_reconciliation') or change.action != 'weekly'
            or change.dry_run is not False or cfg.dry_run or cfg.wiw_mode != 'live'):
        raise ValueError('This command requires an incomplete approved live weekly request.')
    if (effective_date != change.proposed['effective_date'] or date.fromisoformat(effective_date) <= local_today()
            or not employee.active or employee.wiw_user_id != change.wiw_user_id
            or set(assigned_locations(employee)) != set(assigned_locations(change))):
        raise ValueError('The date or employee mapping does not match the approved request.')
    if timeline(db, employee.id, False):
        raise ValueError('This recovery is limited to an initial schedule; an approved timeline already exists.')
    if db.scalar(select(Audit.id).where(Audit.change_id == request_id, Audit.event == 'recovery_plan')):
        raise ValueError('A replacement recovery was already attempted. Inspect it; do not repeat writes.')
    entries = db.scalars(select(Audit).where(Audit.change_id == request_id).order_by(Audit.id)).all()
    if not any(e.event == 'approved' for e in entries): raise ValueError('No saved approval exists.')
    last = max(e.created.replace(tzinfo=timezone.utc) if e.created.tzinfo is None else e.created for e in entries)
    if now() - last < timedelta(minutes=10):
        raise ValueError('Recent request activity: wait until there has been no activity for 10 minutes. No writes performed.')
    def absent(event_id):
        try: provider.get(event_id, change.wiw_user_id)
        except WIWError as exc:
            if exc.reason == 'http_error' and exc.http_status == 404: return
            raise
        raise ValueError(f'Original created event {event_id} still exists. Inspect before replacement.')
    # This recovery addresses the observed case: all journaled creations are gone.
    for entry in entries:
        if entry.event == 'operation_succeeded':
            event = entry.details.get('response', {}).get('availabilityevent')
            if isinstance(event, dict) and type(event.get('id')) is int: absent(event['id'])
    managed = db.scalars(select(ManagedEvent).where(ManagedEvent.employee_id == employee.id,
        ManagedEvent.active.is_(True))).all()
    for item in managed: absent(item.event_id)
    before = provider.read(change.wiw_user_id, change.read_start, change.read_end)
    edits, retained = handover(before['availabilityevents'], date.fromisoformat(effective_date))
    payloads = event_plan([change.proposed])
    operations = [{'action':'create','payload':p,'rfc_dates':True} for p in payloads] + edits
    # Recheck after planning; no deletes are performed until all new entries
    # have been created, read back, and the complete state matches expectations.
    if canonical(provider.read(change.wiw_user_id, change.read_start, change.read_end)) != canonical(before):
        raise ValueError('WIW changed during inspection. No writes performed.')
    details = {'pre_write_state':before,'managed_event_snapshots':[],
        'retained_external_events':retained,'operations':operations,
        'weekly_schedule':change.proposed,'preserve_before':effective_date}
    audit(db, change, actor, 'recovery_plan', details)
    change.status = 'applying'
    db.commit()
    created = []
    def verify_state(expected):
        actual = provider.read(change.wiw_user_id, change.read_start, change.read_end)['availabilityevents']
        if Counter(map(event_signature, actual)) != Counter(map(event_signature, expected)):
            raise WIWError('WIW differs from the expected recovery state. Stop and inspect.', reason='recovery_state_mismatch')
        if {e['id'] for e in actual} != {e['id'] for e in expected}:
            raise WIWError('WIW event identities changed during recovery.', reason='recovery_state_mismatch')
    index = -1
    try:
        for index, op in enumerate(operations):
            if index == len(payloads): verify_state(before['availabilityevents'] + created)
            if op['action'] != 'create':
                original = next(e for e in before['availabilityevents'] if e['id'] == op['event_id'])
                if provider.get(op['event_id'], change.wiw_user_id) != original:
                    raise WIWError('An existing WIW entry changed before replacement.')
            audit(db, change, actor, 'operation_started', {'index':index,'operation':op,'recovery':True})
            db.commit()
            result = provider.weekly_operation(change, op)
            if op['action'] == 'create':
                event = result['availabilityevent']
                observed = provider.get(event['id'], change.wiw_user_id)
                provider.verify_payload(observed, op['payload'])
                created.append(observed)
            elif op['action'] == 'delete':
                try: provider.get(op['event_id'], change.wiw_user_id)
                except WIWError as exc:
                    if exc.reason != 'http_error' or exc.http_status != 404: raise
                else: raise WIWError('Deleted event is still present.')
            audit(db, change, actor, 'operation_succeeded', {'index':index,'response':result,'recovery':True})
            db.commit()
        verify_state(retained + created)
    except (WIWError, ValueError, KeyError, TypeError) as exc:
        change.status = 'needs_reconciliation'
        audit(db, change, actor, 'write_uncertain', {'index':index,'recovery':True,
            'reason':str(exc) if isinstance(exc, WIWError) else 'Recovery response could not be verified.'})
        db.commit()
        return change
    for item in managed: item.active = False
    for event in created:
        db.add(ManagedEvent(employee_id=employee.id,event_id=event['id'],snapshot=event))
    db.add(WeeklySchedule(change_id=change.id,employee_id=employee.id,
        effective_date=date.fromisoformat(effective_date),days=change.proposed['days'],dry_run=False))
    change.status = 'applied'
    audit(db, change, actor, 'applied', {'recovery':True,'operation_count':len(operations),
        'preserved_event_ids':[e['id'] for e in retained]})
    db.commit()
    return change
