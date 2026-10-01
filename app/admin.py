from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select, delete, func, or_
from .db import get_db
from .models import User, Scope, Change, AdminAudit, Location, EmailOutbox, WebhookBatch
from .security import current_user, csrf

from .locations import assigned_locations, location_choices, lock_admin_changes, check_admin_cap
from .notifications import valid_email

router = APIRouter()

def require_admin(request, db):
    user = current_user(request, db)
    if user.role != 'admin':
        raise HTTPException(403, 'Administrator access is required.')
    return user

def directory_filters(request):
    from urllib.parse import urlencode
    values={k:request.query_params.get(k,'').strip()[:254] for k in ('q','location','role','status','p')}
    return values, urlencode({k:v for k,v in values.items() if v})

@router.get('/admin')
def admin_page(request: Request, db=Depends(get_db)):
    from .main import page
    from urllib.parse import urlencode
    actor = require_admin(request, db)
    filters, query = directory_filters(request)
    conditions=[]
    if filters['q']:
        term=filters['q'].replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        conditions.append(or_(*(col.ilike('%'+term+'%',escape='\\') for col in (User.name,User.email,User.notification_email))))
    if filters['location']:
        conditions.append(or_(User.location==filters['location'],User.secondary_location==filters['location']))
    if filters['role'] in ('employee','manager','admin'): conditions.append(User.role==filters['role'])
    if filters['status'] in ('active','disabled'): conditions.append(User.active.is_(filters['status']=='active'))
    total=db.scalar(select(func.count()).select_from(User).where(*conditions))
    pages=max(1,(total+24)//25)
    try: number=max(1,min(int(filters['p'] or 1),pages))
    except ValueError: number=1
    filters['p']=str(number)
    query=urlencode({k:v for k,v in filters.items() if v})
    people=list(db.scalars(select(User).where(*conditions).order_by(func.lower(User.name),User.id).offset((number-1)*25).limit(25)))
    def page_url(n): return '/admin?'+urlencode({**{k:v for k,v in filters.items() if v},'p':n})
    history=list(db.scalars(select(AdminAudit).order_by(AdminAudit.id.desc()).limit(30)))
    ids={v for h in history for v in (h.actor_id,h.target_id)}
    names=dict(db.execute(select(User.id,User.name).where(User.id.in_(ids))).all()) if ids else {}
    return page(request,'admin.html',user=actor,people=people,history=history,names=names,
        notification_scopes=sorted(set(db.scalars(select(Scope.location).where(Scope.manager_id==actor.id))) | set(assigned_locations(actor))),
        locations=location_choices(db),filters=filters,directory_query=query,total=total,page_number=number,pages=pages,
        previous_url=page_url(number-1),next_url=page_url(number+1),
        admin_count=db.scalar(select(func.count()).select_from(User).where(User.role=='admin')),
        webhook_counts=dict(db.execute(select(WebhookBatch.status,func.count()).group_by(WebhookBatch.status)).all()),
        latest_webhook=db.scalar(select(WebhookBatch.created).order_by(WebhookBatch.created.desc()).limit(1)),
        mail_counts=dict(db.execute(select(EmailOutbox.status,func.count()).group_by(EmailOutbox.status)).all()))

@router.get('/admin/users/{user_id}')
def person_page(request: Request,user_id:int,db=Depends(get_db)):
    from .main import page
    actor=require_admin(request,db)
    person=db.get(User,user_id)
    if not person: raise HTTPException(404,'Employee not found.')
    _,query=directory_filters(request)
    history=list(db.scalars(select(AdminAudit).where(AdminAudit.target_id==user_id).order_by(AdminAudit.id.desc()).limit(20)))
    ids={h.actor_id for h in history}
    names=dict(db.execute(select(User.id,User.name).where(User.id.in_(ids))).all()) if ids else {}
    return page(request,'admin_person.html',user=actor,person=person,locations=location_choices(db),
        scopes={person.id:list(db.scalars(select(Scope.location).where(Scope.manager_id==user_id)))},
        directory_query=query,back_url='/admin'+('?' + query if query else ''),history=history,names=names)

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
    return RedirectResponse(f'/admin/users/{user_id}?'+directory_filters(request)[1]+'&saved=1',303)


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


@router.post('/admin/users/{user_id}/rules')
async def update_rules(request:Request,user_id:int,db=Depends(get_db)):
    from decimal import Decimal, InvalidOperation
    actor=require_admin(request,db)
    form=await request.form(max_fields=10)
    csrf(request,form.get('csrf'))
    try:
        minutes=Decimal(str(form.get('minimum_hours','15')))*60
        notice=int(str(form.get('notice_days','14')))
        if not minutes.is_finite() or minutes!=minutes.to_integral_value() or not 0<=minutes<=4200 or not 1<=notice<=366: raise ValueError()
    except (ValueError,InvalidOperation):
        raise HTTPException(422,'Choose 0–70 minimum hours (whole minutes) and 1–366 notice days.')
    lock_admin_changes(db);db.refresh(actor)
    if actor.role!='admin' or not actor.active: raise HTTPException(403,'Administrator access is required.')
    person=db.scalar(select(User).where(User.id==user_id).with_for_update())
    if not person: raise HTTPException(404,'Employee not found.')
    keys=('minimum_hours_enabled','minimum_available_minutes','notice_enabled','notice_days')
    before={k:getattr(person,k) for k in keys}
    person.minimum_hours_enabled=form.get('minimum_hours_enabled')=='on'
    person.minimum_available_minutes=int(minutes)
    person.notice_enabled=form.get('notice_enabled')=='on'
    person.notice_days=notice
    db.add(AdminAudit(actor_id=actor.id,target_id=person.id,details={'event':'availability_rules_updated','before':before,'after':{k:getattr(person,k) for k in keys}}))
    db.commit()
    return RedirectResponse(f'/admin/users/{user_id}?'+directory_filters(request)[1]+'&saved=1',303)

@router.post('/admin/my-notifications')
async def my_notifications(request: Request, db=Depends(get_db)):
    actor = require_admin(request, db)
    form = await request.form(max_fields=200)
    csrf(request, form.get('csrf'))
    locations = sorted(set(form.getlist('notification_locations')))
    permitted = set(db.scalars(select(Scope.location).where(Scope.manager_id==actor.id))) | set(assigned_locations(actor))
    if not set(locations) <= permitted:
        raise HTTPException(422, 'Choose only your own or approval locations.')
    before = {'enabled':actor.notifications_enabled, 'locations':actor.notification_locations}
    actor.notifications_enabled = form.get('notifications_enabled') == 'on'
    actor.notification_locations = locations
    db.add(AdminAudit(actor_id=actor.id,target_id=actor.id,details={
        'event':'notification_preferences','before':before,
        'after':{'enabled':actor.notifications_enabled,'locations':locations}}))
    db.commit()
    return RedirectResponse('/admin?notifications_saved=1',303)
