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


def recover_requested_schedule(db, actor, request_id, effective_date, provider, resolve_conflicts=False):
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
    entries = db.scalars(select(Audit).where(Audit.change_id == request_id).order_by(Audit.id)).all()
    plans = [e for e in entries if e.event == 'recovery_plan']
    if plans and not resolve_conflicts:
        raise ValueError('A replacement recovery was already attempted. Inspect it; do not repeat writes.')
    if not any(e.event == 'approved' for e in entries): raise ValueError('No saved approval exists.')
    if not resolve_conflicts:
        last = max(e.created.replace(tzinfo=timezone.utc) if e.created.tzinfo is None else e.created for e in entries)
        if now() - last < timedelta(minutes=10):
            raise ValueError('Recent request activity: wait until there has been no activity for 10 minutes. No writes performed.')
    def absent(event_id):
        try: provider.get(event_id, change.wiw_user_id)
        except WIWError as exc:
            if exc.reason == 'http_error' and exc.http_status == 404: return
            raise
        raise ValueError(f'Original created event {event_id} still exists. Inspect before replacement.')
    managed = db.scalars(select(ManagedEvent).where(ManagedEvent.employee_id == employee.id,
        ManagedEvent.active.is_(True))).all()
    for item in managed: absent(item.event_id)
    created = []
    start_index = 0
    if resolve_conflicts:
        if change.status != 'needs_reconciliation' or len(plans) != 1 or any(e.event == 'recovery_reordered' for e in entries):
            raise ValueError('Conflict continuation requires a single stopped replacement recovery that has not been continued.')
        plan = plans[0]
        attempts = [e for e in entries if e.id > plan.id]
        failures = [e for e in attempts if e.event == 'write_uncertain']
        if (len(failures) != 1 or not failures[0].details.get('recovery')
                or 'HTTP 409 (WIW code 4090)' not in failures[0].details.get('reason','')):
            raise ValueError('This continuation is limited to a verified WIW availability-conflict rejection.')
        start_index = failures[0].details.get('index')
        operations = plan.details['operations']
        if type(start_index) is not int or not 0 <= start_index < len(operations):
            raise ValueError('Invalid recovery journal.')
        starts = [e for e in attempts if e.event == 'operation_started']
        successes = [e for e in attempts if e.event == 'operation_succeeded']
        if len(starts) != start_index + 1 or len(successes) != start_index:
            raise ValueError('Incomplete recovery journal. No writes performed.')
        for i, entry in enumerate(starts):
            if (entry.details.get('index') != i or entry.details.get('operation') != operations[i]
                    or operations[i]['action'] != 'create'):
                raise ValueError('Only a creation prefix followed by a rejected create can be continued.')
        for i, entry in enumerate(successes):
            if entry.details.get('index') != i: raise ValueError('Unexpected recovery journal ordering.')
            saved = entry.details['response']['availabilityevent']
            event = provider.get(saved['id'], change.wiw_user_id)
            provider.verify_payload(event, operations[i]['payload'])
            created.append(event)
        before = plan.details['pre_write_state']
        retained = plan.details['retained_external_events']
        edits = [op for op in operations if op['action'] != 'create']
        from dateutil.parser import parse
        from zoneinfo import ZoneInfo
        cutoff = date.fromisoformat(effective_date)
        for op in edits:
            source = next((e for e in before['availabilityevents'] if e['id'] == op.get('event_id')), None)
            if (op['action'] != 'delete' or not source
                    or parse(source['start_time']).astimezone(ZoneInfo(cfg.business_timezone)).date() < cutoff):
                raise ValueError('Conflict continuation can only remove entries starting on or after the approved date.')
        # Account for every current entry, including the rejected create: any
        # extra or changed entry blocks continuation rather than being guessed away.
        expected = {'availabilityevents':before['availabilityevents'] + created}
        if canonical(provider.read(change.wiw_user_id, change.read_start, change.read_end)) != canonical(expected):
            raise ValueError('WIW differs from the original entries plus verified creations. No writes performed.')
        remaining_creates = [op for op in operations[start_index:] if op['action'] == 'create']
        operations = operations[:start_index] + edits + remaining_creates
        payloads = [op['payload'] for op in operations if op['action'] == 'create']
        audit(db, change, actor, 'recovery_reordered', {'operations':operations,
            'verified_created_ids':[e['id'] for e in created], 'next_operation':start_index,
            'source_plan_id':plan.id,'preserve_before':effective_date})
    else:
        for entry in entries:
            if entry.event == 'operation_succeeded':
                event = entry.details.get('response', {}).get('availabilityevent')
                if isinstance(event, dict) and type(event.get('id')) is int: absent(event['id'])
        before = provider.read(change.wiw_user_id, change.read_start, change.read_end)
        edits, retained = handover(before['availabilityevents'], date.fromisoformat(effective_date))
        payloads = event_plan([change.proposed])
        operations = [{'action':'create','payload':p,'rfc_dates':True} for p in payloads] + edits
        if canonical(provider.read(change.wiw_user_id, change.read_start, change.read_end)) != canonical(before):
            raise ValueError('WIW changed during inspection. No writes performed.')
        details = {'pre_write_state':before,'managed_event_snapshots':[],
            'retained_external_events':retained,'operations':operations,
            'weekly_schedule':change.proposed,'preserve_before':effective_date}
        audit(db, change, actor, 'recovery_plan', details)
    change.status = 'applying'
    db.commit()
    def verify_state(expected):
        actual = provider.read(change.wiw_user_id, change.read_start, change.read_end)['availabilityevents']
        if Counter(map(event_signature, actual)) != Counter(map(event_signature, expected)):
            raise WIWError('WIW differs from the expected recovery state. Stop and inspect.', reason='recovery_state_mismatch')
        if {e['id'] for e in actual} != {e['id'] for e in expected}:
            raise WIWError('WIW event identities changed during recovery.', reason='recovery_state_mismatch')
    index = -1
    try:
        for index, op in enumerate(operations[start_index:], start=start_index):
            if not resolve_conflicts and index == len(payloads): verify_state(before['availabilityevents'] + created)
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
