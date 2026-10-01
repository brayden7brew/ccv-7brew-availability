"""Read the configured WIW workplace roster; never write to WIW."""
from sqlalchemy import select
from .config import settings
from .models import User, AdminAudit
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
    added = 0
    for record in records:
        if record.get('activated') is not True or record.get('is_deleted') is not False:
            continue
        if db.scalar(select(User.id).where(User.wiw_user_id == record['id'])):
            continue
        name = ' '.join(str(record.get(k) or '').strip() for k in ('first_name', 'last_name')).strip()
        address = record.get('email')
        address = address.strip().lower() if isinstance(address, str) else ''
        person = User(wiw_user_id=record['id'],
            email=f"wiw-{cfg.wiw_account_id}-{record['id']}@portal.invalid",
            name=(name or f"Employee {record['id']}")[:200], password_hash='!',
            role='employee', active=True, location=cfg.wiw_auto_enroll_location,
            notification_email=address if valid_email(address) else '')
        db.add(person)
        db.flush()
        db.add(AdminAudit(actor_id=actor.id, target_id=person.id,
            details={'event': 'wiw_employee_imported', 'account_id': cfg.wiw_account_id}))
        added += 1
    return added
