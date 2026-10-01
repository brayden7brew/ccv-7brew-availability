from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select, delete, func
from .db import get_db
from .models import User, Scope, Change, AdminAudit, Location, EmailOutbox, WebhookBatch
from .security import current_user, csrf

from .locations import location_choices, lock_admin_changes, check_admin_cap
from .notifications import valid_email

router = APIRouter()

def require_admin(request, db):
    user = current_user(request, db)
    if user.role != 'admin':
        raise HTTPException(403, 'Administrator access is required.')
    return user

@router.get('/admin')
def admin_page(request: Request, db=Depends(get_db)):
    from .main import page
    actor = require_admin(request, db)
    people = list(db.scalars(select(User).order_by(User.name)))
    scopes = {u.id:list(db.scalars(select(Scope.location).where(Scope.manager_id == u.id).order_by(Scope.location))) for u in people}
    history = list(db.scalars(select(AdminAudit).order_by(AdminAudit.id.desc()).limit(30)))
    return page(request, 'admin.html', user=actor, people=people, scopes=scopes, history=history,
                names={u.id:u.name for u in people}, locations=location_choices(db),
                admin_count=sum(u.role=='admin' for u in people),
                webhook_counts=dict(db.execute(select(WebhookBatch.status,func.count()).group_by(WebhookBatch.status)).all()),
                latest_webhook=db.scalar(select(WebhookBatch.created).order_by(WebhookBatch.created.desc()).limit(1)),
                mail_counts=dict(db.execute(select(EmailOutbox.status,func.count()).group_by(EmailOutbox.status)).all()))

@router.post('/admin/users/{user_id}')
async def update_user(request: Request, user_id: int, db=Depends(get_db)):
    actor = require_admin(request, db)
    form = await request.form(max_fields=150)
    csrf(request, form.get('csrf'))
    lock_admin_changes(db)
    db.refresh(actor)
    if actor.role != "admin": raise HTTPException(403, "Administrator access is required.")
    target = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if not target: raise HTTPException(404, 'Employee not found.')
    role = str(form.get('role', ''))
    location = str(form.get('location', '')).strip()
    secondary = str(form.get('secondary_location','')).strip()
    notification_email = str(form.get('notification_email','')).strip().lower()
    scopes = sorted(set(str(v).strip() for v in form.getlist('scopes') if str(v).strip()))
    choices = location_choices(db)
    if role not in ('employee','manager','admin'):
        raise HTTPException(422, 'Choose a valid portal role.')
    if role=='admin' and target.role!='admin': check_admin_cap(db)
    if target.id==actor.id and role!='admin':
        raise HTTPException(422, 'You cannot remove your own administrator access.')
    if location not in choices or (secondary and secondary not in choices) or any(v not in choices for v in scopes):
        raise HTTPException(422, 'Choose locations from the list.')
    if secondary==location: raise HTTPException(422, 'Choose two different locations or leave the second empty.')
    if notification_email and not valid_email(notification_email):
        raise HTTPException(422, 'Enter a valid notification email address.')
    if role == 'employee': scopes = []
    elif role == 'manager' and not scopes:
        raise HTTPException(422, 'Assign at least one approval location to a manager.')
    if (location != target.location or secondary != target.secondary_location) and db.scalar(select(Change.id).where(Change.employee_id==target.id,
            Change.status.in_(['pending','applying','needs_reconciliation']))):
        raise HTTPException(409, 'Resolve this employee’s pending or unconfirmed requests before changing their approval location.')
    before = dict(role=target.role, location=target.location, secondary_location=target.secondary_location, notification_email=target.notification_email,
        scopes=list(db.scalars(select(Scope.location).where(Scope.manager_id==target.id))))
    target.role, target.location = role, location
    target.secondary_location, target.notification_email = secondary, notification_email
    db.execute(delete(Scope).where(Scope.manager_id == target.id))
    for value in scopes: db.add(Scope(manager_id=target.id, location=value))
    if notification_email:
        for item in db.scalars(select(EmailOutbox).where(EmailOutbox.recipient_id==target.id, EmailOutbox.status=='missing_address')):
            item.recipient=notification_email
            item.status='queued'
    db.add(AdminAudit(actor_id=actor.id, target_id=target.id,
        details={'before':before,'after':dict(role=role,location=location,secondary_location=secondary,notification_email=notification_email,scopes=scopes)}))
    db.commit()
    return RedirectResponse('/admin',303)


@router.post('/admin/locations')
async def add_location(request: Request, db=Depends(get_db)):
    actor = require_admin(request, db)
    form = await request.form(max_fields=5)
    csrf(request, form.get('csrf'))
    name = str(form.get('name','')).strip()
    if not name or len(name)>120:
        raise HTTPException(422, 'Enter a location name of 1–120 characters.')
    lock_admin_changes(db)
    db.refresh(actor)
    if actor.role!='admin': raise HTTPException(403,'Administrator access is required.')
    if not db.get(Location,name):
        db.add(Location(name=name))
        db.add(AdminAudit(actor_id=actor.id,target_id=actor.id,details={'location_added':name}))
        db.commit()
    return RedirectResponse('/admin',303)


@router.post('/admin/import-wiw')
async def import_wiw(request: Request, db=Depends(get_db)):
    from .wiw import WIW, WIWError
    from .roster import import_roster, import_schedules
    from sqlalchemy.exc import IntegrityError
    actor = require_admin(request, db)
    form = await request.form(max_fields=5)
    csrf(request, form.get('csrf'))
    lock_admin_changes(db)
    db.refresh(actor)
    if actor.role != 'admin':
        raise HTTPException(403, 'Administrator access is required.')
    try:
        wiw=WIW()
        schedule_count=import_schedules(db, actor, wiw)
        count = 0 if form.get("schedules_only")=="on" else import_roster(db, actor, wiw)
        db.commit()
    except WIWError as exc:
        db.rollback()
        raise HTTPException(502, str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, 'An employee signed in while importing. Run the import again.') from exc
    return RedirectResponse(f'/admin?imported={count}&schedules_imported={schedule_count}', 303)


@router.post('/admin/email-test')
async def email_test(request: Request, db=Depends(get_db)):
    from types import SimpleNamespace
    import secrets
    from .config import settings
    from .mailer import send_email
    from starlette.concurrency import run_in_threadpool
    actor=require_admin(request,db)
    form=await request.form(max_fields=5)
    csrf(request,form.get('csrf'))
    cfg=settings()
    if not cfg.email_enabled: raise HTTPException(422,'Email delivery is disabled.')
    address=actor.notification_email or actor.email
    if not valid_email(address): raise HTTPException(422,'Save your notification email before sending a test.')
    from .main import limit_login
    limit_login(request,db,'email-test:'+str(actor.id))
    item=SimpleNamespace(id='test-'+secrets.token_hex(12),recipient=address,change_id=None,
        subject='CCV 7 Brew Availability — email test',link=cfg.public_base_url.rstrip('/')+'/login',
        link_label='Open CCV 7 Brew Availability',
        body='Your portal can send emails through alerts@rva7brew.com. This test does not change availability.')
    try: await run_in_threadpool(send_email,item)
    except Exception as exc:
        raise HTTPException(502,'Email delivery could not be confirmed. Check the Microsoft mail configuration and your inbox before trying again.') from None
    db.add(AdminAudit(actor_id=actor.id,target_id=actor.id,details={'event':'test_email_accepted'}))
    db.commit()
    return RedirectResponse('/admin?email_test=accepted',303)
