"""Only fixed reason codes are stored; never credentials, tokens or provider bodies."""
from datetime import timedelta, timezone
from sqlalchemy import delete
from .models import LoginEvent, PortalSetup, now
from .security import digest

LABELS = {
    'success': 'Signed in successfully',
    'credentials': 'Email or password not accepted',
    'disabled': 'Portal account disabled',
    'not_enabled': 'Portal access not enabled',
    'limited': 'Too many sign-in attempts — wait 15 minutes',
    'wiw_limited': 'When I Work is limiting sign-in attempts',
    'wiw_rejected': 'When I Work could not complete sign-in (password, two-step verification or SSO may be involved)',
    'membership': 'No active membership in this workplace',
    'unavailable': 'When I Work sign-in temporarily unavailable',
    'not_configured': 'When I Work sign-in not configured',
}

def record(db, email, method, outcome, user=None):
    assert method in ('portal', 'wiw') and outcome in LABELS
    db.execute(delete(LoginEvent).where(LoginEvent.created < now()-timedelta(days=30)).execution_options(synchronize_session=False))
    db.add(LoginEvent(email=email[:254], method=method, outcome=outcome,
                      user_id=user.id if user else None))
    db.commit()


def setup_status(db, person):
    setup = db.get(PortalSetup, person.id)
    if setup:
        if setup.credential_version != digest(person.email+'\n'+person.password_hash):
            return 'Setup code replaced or invalidated. Create a new code.'
        if setup.expires.replace(tzinfo=timezone.utc) <= now():
            return 'Setup code expired. Create a new code.'
        return 'Setup pending: employee has not redeemed the current code (expires within 24 hours of issue).'
    if person.password_hash.startswith('$argon2'):
        return 'Portal password is set.'
    return 'No separate portal password set.'
