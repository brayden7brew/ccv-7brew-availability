"""Explicit recovery of a delete-only prefix that left a historical series tail.

Never repeat DELETE: preserve verified history, restore missing old occurrences,
cap remaining series, and publish the approved future schedule. Preview by default.
"""
from collections import Counter
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select
from .config import settings
from .models import User, Change, Audit, ManagedEvent
from .weekly import local_today, timeline, timeline_ids, display_schedule, event_plan
from .workflow import allowed, audit, canonical
from .locations import assigned_locations
from .event_identity import event_signature, finite_occurrences
from .legacy_availability import handover
from .weekly_workflow import dispatch_weekly
from .wiw import WIWError


def replacement_plan(original, current, deleted, truncated, effective, future, recovery_day):
    """Validate observed changes, then preserve the original prefix exactly."""
    cutoff = datetime.combine(effective, time(), ZoneInfo(settings().business_timezone))
    old = {e['id']: e for e in original}
    live = {e['id']: e for e in current}
    if (len(old) != len(original) or len(live) != len(current)
            or truncated not in old or truncated in deleted
            or not deleted <= old.keys() or live.keys() != old.keys()-deleted):
        raise ValueError('Unexpected missing or additional WIW records. No writes performed.')
    for key, event in live.items():
        if key != truncated:
            if event != old[key]:
                raise ValueError('An unattempted event changed. No writes performed.')
            continue
        # Only accept the observed DELETE behavior: unchanged hours with an
        # absolute recurrence end. Do not excuse other edits or added exclusions.
        original_rule = (old[key].get('recurrence') or '').removeprefix('RRULE:').upper().split(';')
        current_rule = (event.get('recurrence') or '').removeprefix('RRULE:').upper().split(';')
        until = [p for p in current_rule if p.startswith('UNTIL=')]
        if (len(until) != 1 or any(p.startswith('COUNT=') for p in current_rule)
                or sorted(p for p in original_rule if not p.startswith(('COUNT=', 'UNTIL='))) !=
                   sorted(p for p in current_rule if not p.startswith('UNTIL='))
                or event_signature(event)[:4] != event_signature(old[key])[:4]
                or any(event.get(k) != old[key].get(k) for k in ('notes','user_id','account_id'))):
            raise ValueError('The attempted delete is not a verified recurrence truncation.')
        historical = finite_occurrences([event])
        if not historical or historical - finite_occurrences([old[key]], before=cutoff):
            raise ValueError('The retained series is not wholly within the approved earlier hours.')
    edits, retained = handover(current, effective)
    if any(op['action'] != 'update' for op in edits):
        raise ValueError('Recovery would require another delete. Review manually.')
    wanted = finite_occurrences(original, before=cutoff)
    kept = finite_occurrences(retained)
    if kept - wanted:
        raise ValueError('Current availability adds or changes earlier hours.')
    missing = wanted-kept
    today_start = datetime.combine(recovery_day, time(), cutoff.tzinfo)
    past_missing = Counter({key: count for key, count in missing.items()
                            if datetime.fromisoformat(key[2]) <= today_start})
    missing -= past_missing
    if any(datetime.fromisoformat(key[1]) < today_start for key in missing):
        raise ValueError('A missing occurrence crosses today’s boundary; review manually.')
    if sum(missing.values()) > 100:
        raise ValueError('Too many missing historical occurrences for this recovery.')
    restores=[]
    for (kind, start, end, all_day, notes), count in missing.items():
        if count != 1:
            raise ValueError('Duplicate historical occurrences need manual review.')
        restores.append({'type':kind,'start_time':start,'end_time':end,
                         'all_day':all_day,'notes':notes})
    if finite_occurrences(retained+restores)+past_missing != wanted:
        raise ValueError('Historical restoration does not match the original hours.')
    if any(datetime.fromisoformat(p['start_time']) < cutoff for p in future):
        raise ValueError('New schedule begins before the approved start date.')
    for op in edits: op['rfc_dates']=True
    operations = ([{'action':'create','payload':p,'rfc_dates':True} for p in restores] + edits +
                  [{'action':'create','payload':p,'rfc_dates':True} for p in future])
    return operations, retained, len(restores), list(past_missing.elements())


def repair_truncated_delete(db, actor, change_id, provider, apply=False):
    existing=db.get(Change,change_id)
    if not existing: raise ValueError('Request not found.')
    employee=db.scalar(select(User).where(User.id==existing.employee_id).with_for_update())
    change=db.scalar(select(Change).where(Change.id==change_id).with_for_update().execution_options(populate_existing=True))
    cfg=settings()
    if (not actor.active or actor.role not in ('manager','admin') or actor.id==employee.id
            or (actor.role!='admin' and not allowed(db,actor,change))):
        raise ValueError('An authorized manager or administrator is required.')
    effective=date.fromisoformat(change.proposed['effective_date'])
    if (change.action!='weekly' or change.status!='needs_reconciliation' or change.dry_run is not False
            or change.manager_id is None or cfg.dry_run or cfg.wiw_mode!='live'
            or effective<=local_today() or not employee.active or employee.wiw_user_id!=change.wiw_user_id
            or set(assigned_locations(employee))!=set(assigned_locations(change))):
        raise ValueError('Request status, live mode, employee mapping or start date prevents recovery.')
    if db.scalar(select(Change.id).where(Change.employee_id==employee.id, Change.id!=change.id,
            Change.status.in_(['queued','preparing','applying','needs_reconciliation']))):
        raise ValueError('Another unresolved approval exists.')
    rows=timeline(db,employee.id,False)
    if timeline_ids(rows)!=change.before['timeline_ids'] or any(r.effective_date>=effective for r in rows):
        raise ValueError('Approved timeline changed or includes another future schedule.')
    entries=list(db.scalars(select(Audit).where(Audit.change_id==change.id).order_by(Audit.id)))
    approvals=[e for e in entries if e.event in ('approved','recovery_plan','recovery_verified')]
    if len(approvals)!=1 or approvals[0].event!='approved':
        raise ValueError('An original approval without previous recovery attempts is required.')
    approval=approvals[0]; ops=approval.details['operations']
    journal=[e for e in entries if e.id>approval.id and e.event in ('operation_started','operation_succeeded','write_uncertain')]
    if len(journal)<2 or len(journal)%2:
        raise ValueError('Unexpected operation journal.')
    count=len(journal)//2
    for i in range(count):
        started,finished=journal[2*i:2*i+2]
        if (i>=len(ops) or ops[i]['action']!='delete' or started.event!='operation_started'
                or started.details.get('index')!=i or started.details.get('operation')!=ops[i]
                or finished.details.get('index')!=i
                or finished.event!=('write_uncertain' if i==count-1 else 'operation_succeeded')):
            raise ValueError('Recovery requires successful deletes followed by one uncertain delete.')
    original=approval.details['pre_write_state']['availabilityevents']
    original_ids={e['id'] for e in original}
    if (approval.details.get('retained_external_events') or
            {op['event_id'] for op in ops if op['action']=='delete'}!=original_ids or
            any(op['action'] not in ('delete','create') for op in ops)):
        raise ValueError('Approval is not a complete managed-schedule replacement.')
    approved_day=approval.created.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(cfg.business_timezone)).date()
    expected=event_plan([display_schedule(r) for r in rows]+[change.proposed], today=approved_day)
    if Counter(map(event_signature,expected))!=Counter(event_signature(op['payload']) for op in ops if op['action']=='create'):
        raise ValueError('Saved operations do not match the approved timeline.')
    managed=list(db.scalars(select(ManagedEvent).where(ManagedEvent.employee_id==employee.id)))
    if any(m.active and m.event_id not in original_ids for m in managed):
        raise ValueError('Unexpected managed availability exists.')
    deleted={ops[i]['event_id'] for i in range(count-1)}
    for key in deleted:
        try: provider.get(key,change.wiw_user_id)
        except WIWError as exc:
            if exc.reason!='http_error' or exc.http_status!=404: raise
        else: raise ValueError('A previously deleted event is present.')
    current=provider.read(change.wiw_user_id,change.read_start,change.read_end)
    for event in current['availabilityevents']:
        if provider.get(event['id'],change.wiw_user_id)!=event:
            raise ValueError('WIW changed during inspection.')
    operations,retained,restored,past_missing=replacement_plan(original,current['availabilityevents'],deleted,
        ops[count-1]['event_id'],effective,event_plan([change.proposed]),local_today())
    if canonical(provider.read(change.wiw_user_id,change.read_start,change.read_end))!=canonical(current):
        raise ValueError('WIW changed during inspection.')
    if not apply:
        return {'request':change.id,'start':effective.isoformat(),'restore_occurrences':restored,
                'past_missing_occurrences_not_recreated':len(past_missing),
                'updates':sum(op['action']=='update' for op in operations),
                'new_schedule_events':len(event_plan([change.proposed])), 'writes_performed':False}
    audit(db,change,actor,'recovery_plan',{'repair':'truncated_delete','approval_id':approval.id,
        'pre_write_state':current,'managed_event_snapshots':approval.details['managed_event_snapshots'],
        'retained_external_events':retained,'operations':operations,'weekly_schedule':change.proposed,
        'preserve_before':effective.isoformat(),
        'historical_missing_occurrences_not_recreated':past_missing,
        'restoration_from':local_today().isoformat()})
    for item in managed:
        if item.event_id in original_ids: item.active=False
    change.status='applying'
    db.commit()
    return dispatch_weekly(db,actor,change,operations,provider,preserved_events=retained)
