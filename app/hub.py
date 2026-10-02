"""Shared portal entry point and permission-checked Ops views.

Reporting continues in the Ops service. Its integration credential never goes
into HTML, browser storage, or a user's cookie.
"""
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from .config import settings
from .db import get_db
from .security import current_user, csrf
from .models import User, AdminAudit
from sqlalchemy import select

router = APIRouter()
STANDS = {
    'le-gordon': 'LeGordon', 'hull-street': 'Hull Street',
    'courthouse': 'Courthouse', 'colonial-heights': 'Colonial Heights', 'ashland': 'Ashland',
}

def permitted_stands(user):
    if user.role == 'admin':
        return list(STANDS)
    if not user.ops_access:
        return []
    return [slug for slug in STANDS if slug in (user.ops_locations or [])]


def ops_user(request, db):
    user = current_user(request, db)
    if not permitted_stands(user):
        raise HTTPException(403, 'Ops dashboard access is not enabled for your account.')
    return user


@router.get('/')
def home(request: Request, db=Depends(get_db)):
    from .main import page, availability_context
    user = current_user(request, db)
    context = availability_context(request, db, user) if user.availability_access or user.role == 'admin' else {}
    return page(request, 'home.html', user=user, has_ops=bool(permitted_stands(user)), **context)


@router.get('/ops')
def dashboard(request: Request, db=Depends(get_db)):
    from .main import page
    user = ops_user(request, db)
    return page(request, 'ops/dashboard.html', user=user)


@router.get('/ops/stands/{slug}')
def stand(request: Request, slug: str, db=Depends(get_db)):
    from .main import page
    user = ops_user(request, db)
    if slug not in permitted_stands(user):
        raise HTTPException(403, 'You do not have access to this stand’s dashboard.')
    return page(request, 'ops/stand.html', user=user, stand_slug=slug, stand_name=STANDS[slug])


def read_ops(slugs):
    cfg = settings()
    if not cfg.ops_backend_url or not cfg.ops_integration_key:
        raise HTTPException(503, 'The Ops dashboard connection is not configured yet.')
    try:
        response = httpx.get(cfg.ops_backend_url.rstrip('/') + '/api/integration/dashboard',
            headers={'Authorization': 'Bearer ' + cfg.ops_integration_key},
            params={'stands': ','.join(slugs)}, timeout=10, follow_redirects=False)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get('stands'), list):
            raise ValueError('Unexpected response')
        # Defense in depth: never return ungranted stands even if the upstream
        # service accidentally returns its full dashboard.
        return [item for item in data['stands'] if isinstance(item, dict) and item.get('slug') in slugs]
    except (httpx.HTTPError, ValueError, TypeError):
        raise HTTPException(503, 'Ops data is temporarily unavailable. Please try again shortly.') from None


@router.get('/ops/api/dashboard')
def dashboard_data(request: Request, db=Depends(get_db)):
    user = ops_user(request, db)
    return {'stands': read_ops(permitted_stands(user))}


@router.get('/ops/api/stands/{slug}')
def stand_data(request: Request, slug: str, db=Depends(get_db)):
    user = ops_user(request, db)
    if slug not in permitted_stands(user):
        raise HTTPException(403, 'You do not have access to this stand’s dashboard.')
    for item in read_ops([slug]):
        if item['slug'] == slug:
            return item
    raise HTTPException(503, 'Stand data is temporarily unavailable.')


@router.post('/admin/users/{user_id}/modules')
async def save_modules(request: Request, user_id: int, db=Depends(get_db)):
    from .admin import require_admin, lock_admin_changes
    from fastapi.responses import RedirectResponse
    actor = require_admin(request, db)
    form = await request.form(max_fields=20)
    csrf(request, form.get('csrf'))
    lock_admin_changes(db)
    db.refresh(actor)
    if actor.role != 'admin' or not actor.active:
        raise HTTPException(403, 'Administrator access is required.')
    person = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if not person:
        raise HTTPException(404, 'Person not found.')
    locations = sorted(set(form.getlist('ops_locations')))
    if any(slug not in STANDS for slug in locations):
        raise HTTPException(422, 'Choose stands from the list.')
    enabled = form.get('ops_access') == 'on'
    if enabled and not locations and person.role != 'admin':
        raise HTTPException(422, 'Select at least one stand for Ops access.')
    before = dict(availability_access=person.availability_access, ops_access=person.ops_access, ops_locations=person.ops_locations)
    person.availability_access = form.get('availability_access') == 'on'
    person.ops_access, person.ops_locations = enabled, locations
    db.add(AdminAudit(actor_id=actor.id, target_id=person.id, details={
        'action': 'module_access', 'before': before,
        'after': dict(availability_access=person.availability_access, ops_access=enabled, ops_locations=locations)}))
    db.commit()
    return RedirectResponse(f'/admin/users/{person.id}?saved=1', 303)
