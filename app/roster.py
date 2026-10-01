"""Read the configured WIW workplace roster; never write to WIW."""
from sqlalchemy import select
from .config import settings
from .models import User, AdminAudit, Change
from .notifications import valid_email
from .wiw import WIWError


def import_roster(db, actor, wiw):
    cfg = settings()
    if cfg.wiw_mode != 'live':
        raise WIWError('Employee import requires a live WIW connection.')
    result = wiw.call('GET', '/2/users', params={'show_pending': 'false', 'show_deleted': 'false'})
    records = result.get('users')
    if not isinstance(records, list):
        raise WIWError('WIW returned an unexpected employee list.')
    # Validate the complete response before creating any portal accounts.
    seen = set()
    for record in records:
        if (not isinstance(record, dict) or type(record.get('id')) is not int
                or record['id'] <= 0 or record['id'] in seen
                or type(record.get('account_id')) is not int
                or record['account_id'] != cfg.wiw_account_id):
            raise WIWError('WIW returned an unexpected workplace or employee list. No employees imported.')
        seen.add(record['id'])
    schedules = schedule_map(wiw)
    added = 0
    for record in records:
        if record.get('activated') is not True or record.get('is_deleted') is not False:
            continue
        existing = db.scalar(select(User).where(User.wiw_user_id == record['id']).with_for_update())
        if existing:
            sync_locations(db, actor, existing, record, schedules)
            continue
        name = ' '.join(str(record.get(k) or '').strip() for k in ('first_name', 'last_name')).strip()
        address = record.get('email')
        address = address.strip().lower() if isinstance(address, str) else ''
        person = User(wiw_user_id=record['id'],
            email=f"wiw-{cfg.wiw_account_id}-{record['id']}@portal.invalid",
            name=(name or f"Employee {record['id']}")[:200], password_hash='!',
            role='employee', active=True, location='', secondary_location='',
            notification_email=address if valid_email(address) else '')
        db.add(person)
        db.flush()
        sync_locations(db, actor, person, record, schedules)
        db.add(AdminAudit(actor_id=actor.id, target_id=person.id,
            details={'event': 'wiw_employee_imported', 'account_id': cfg.wiw_account_id}))
        added += 1
    return added


def schedule_map(wiw):
    """Validate schedule IDs and workplace before using them for approval routing."""
    cfg=settings()
    if cfg.wiw_mode!='live': raise WIWError('Schedule import requires a live WIW connection.')
    records=wiw.call('GET','/2/locations').get('locations')
    if not isinstance(records,list): raise WIWError('WIW returned an unexpected schedule list.')
    seen=set()
    schedules={}
    for row in records:
        if (not isinstance(row,dict) or type(row.get('id')) is not int or row['id']<=0
                or row['id'] in seen or type(row.get('account_id')) is not int
                or row['account_id']!=cfg.wiw_account_id):
            raise WIWError('WIW returned an unexpected workplace or schedule. No schedules imported.')
        seen.add(row['id'])
        if row.get('is_deleted') is not False or row.get('deleted_at'): continue
        name=row.get('name')
        if not isinstance(name,str) or not name.strip() or len(name.strip())>120:
            raise WIWError('A WIW schedule has an invalid name. No schedules imported.')
        schedules[row['id']]=name.strip()
    return schedules


def assigned_schedule_pair(record, schedules, current_primary=''):
    ids = record.get('locations')
    if not isinstance(ids, list) or not ids or any(type(i) is not int or i not in schedules for i in ids):
        return None
    names = list(dict.fromkeys(schedules[i] for i in ids))
    stands = [name for name in names if name.casefold() != 'above stand']
    if len(names) > 2:
        return None
    if len(stands) > 1:
        if current_primary not in stands:
            return None
        stands.remove(current_primary)
        return current_primary, stands[0]
    primary = stands[0] if stands else names[0]
    return primary, next((name for name in names if name != primary), '')


def sync_locations(db, actor, person, record, schedules):
    pair = assigned_schedule_pair(record, schedules, person.location)
    pending = db.scalar(select(Change.id).where(Change.employee_id == person.id,
        Change.status.in_(['pending', 'applying', 'needs_reconciliation'])))
    before = [person.location, person.secondary_location]
    review = pair is None or bool(pending)
    if not review:
        person.location, person.secondary_location = pair
    if review or before != list(pair):
        db.add(AdminAudit(actor_id=actor.id, target_id=person.id, details={
            'event': 'wiw_schedule_assignments_synced', 'before': before,
            'after': [person.location, person.secondary_location],
            'location_review_required': review}))


def import_schedules(db, actor, wiw):
    """Add active WIW schedule names as approval choices without changing scopes."""
    from .models import Location
    schedules = schedule_map(wiw)
    added=0
    for name in sorted(set(schedules.values())):
        if not db.get(Location,name):
            db.add(Location(name=name));added+=1
    db.add(AdminAudit(actor_id=actor.id,target_id=actor.id,
        details={'event':'wiw_schedules_imported','account_id':settings().wiw_account_id,'added':added}))
    return added
