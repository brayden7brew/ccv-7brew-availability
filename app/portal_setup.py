"""Administrator-issued setup codes are stored only as hashes, never URL parameters."""
import secrets
from datetime import timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError
from .db import get_db
from .models import User, PortalSetup, LoginSession, AdminAudit, now
from .security import csrf, digest, hasher
from .admin import require_admin
from .locations import lock_admin_changes
from .notifications import valid_email
from .config import settings

router=APIRouter()

@router.post('/admin/users/{user_id}/portal-login')
async def issue_setup(request: Request, user_id: int, db=Depends(get_db)):
    from .main import page
    actor=require_admin(request,db)
    form=await request.form(max_fields=5)
    csrf(request,form.get('csrf'))
    email=str(form.get('email','')).strip().lower()
    send_requested=form.get('send_email')=='on'
    if send_requested and not settings().email_enabled:
        raise HTTPException(422,'Email delivery is not enabled yet.')
    if not valid_email(email): raise HTTPException(422,'Enter a valid portal login email.')
    lock_admin_changes(db)
    db.refresh(actor)
    if actor.role!='admin' or not actor.active: raise HTTPException(403,'Administrator access is required.')
    target=db.scalar(select(User).where(User.id==user_id).with_for_update())
    if not target or not target.active: raise HTTPException(404,'Active employee not found.')
    if db.scalar(select(User.id).where(User.email==email,User.id!=user_id)):
        raise HTTPException(409,'That login email belongs to another portal account.')
    code=secrets.token_urlsafe(32)
    existing=db.get(PortalSetup,user_id)
    if existing: db.delete(existing); db.flush()
    db.add(PortalSetup(user_id=user_id,digest=digest(code),email=email,
        expires=now()+timedelta(hours=24),credential_version=digest(target.email+'\n'+target.password_hash)))
    db.add(AdminAudit(actor_id=actor.id,target_id=target.id,details={'event':'portal_setup_issued','login_email':email}))
    db.commit()
    base=settings().public_base_url.rstrip('/') or str(request.base_url).rstrip('/')
    mail_status='not_sent'
    if send_requested:
        from types import SimpleNamespace
        from starlette.concurrency import run_in_threadpool
        from .mailer import send_email
        item=SimpleNamespace(id='setup-'+secrets.token_hex(12), recipient=email,
            subject='Set up your CCV 7 Brew portal password', change_id=None,
            link=base+'/setup',link_label='Create your portal password',
            body=f'Hi {target.name},\n\nYour administrator has enabled separate portal access.\n'
                 f'Open {base}/setup and enter this one-time code:\n\n{code}\n\n'
                 f'Your login email will be {email}. Choose your own password on that page. '
                 'This code expires in 24 hours. Your When I Work password is unchanged. '
                 'If you did not expect this email, contact your manager.')
        try:
            await run_in_threadpool(send_email,item)
            mail_status='accepted'
        except Exception:
            # No provider bodies or setup codes in logs/audits. Delivery might be uncertain.
            mail_status='uncertain'
        db.add(AdminAudit(actor_id=actor.id,target_id=target.id,
            details={'event':'portal_setup_email','status':mail_status}))
        db.commit()
    return page(request,'portal_setup_created.html',user=actor,person=target,email=email,
        setup_url=base+'/setup',setup_code=code,mail_status=mail_status)

@router.get('/setup')
def setup_page(request: Request):
    from .main import page
    return page(request,'portal_setup.html')

@router.post('/setup')
async def redeem_setup(request: Request, db=Depends(get_db)):
    from .main import page, limit_login
    form=await request.form(max_fields=8)
    csrf(request,form.get('csrf'))
    code=str(form.get('code','')).strip()
    password=str(form.get('password',''))
    confirm=str(form.get('confirm',''))
    limit_login(request,db,'portal-setup:'+digest(code[:256]))
    if not 8<=len(password)<=1024 or password!=confirm:
        raise HTTPException(422,'Passwords must match and contain at least 8 characters.')
    if len(code)>128: raise HTTPException(400,'Setup code is invalid or expired. Ask your administrator for a new one.')
    # Locate without locking, then always lock the user before the setup row.
    uid=db.scalar(select(PortalSetup.user_id).where(PortalSetup.digest==digest(code)))
    target=db.scalar(select(User).where(User.id==uid).with_for_update()) if uid else None
    setup=db.scalar(select(PortalSetup).where(PortalSetup.user_id==uid).with_for_update().execution_options(populate_existing=True)) if target else None
    if (not target or not target.active or not setup or setup.digest!=digest(code)
            or setup.expires.replace(tzinfo=timezone.utc)<=now()
            or setup.credential_version!=digest(target.email+'\n'+target.password_hash)):
        raise HTTPException(400,'Setup code is invalid or expired. Ask your administrator for a new one.')
    target.email=setup.email
    target.password_hash=hasher.hash(password)
    db.execute(delete(LoginSession).where(LoginSession.user_id==target.id))
    db.delete(setup)
    db.add(AdminAudit(actor_id=target.id,target_id=target.id,details={'event':'portal_password_created'}))
    try: db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409,'That login email is already in use. Contact your administrator.') from exc
    request.session.clear()
    return page(request,'portal_setup.html',complete=True)
