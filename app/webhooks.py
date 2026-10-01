"""WIW's documented HMAC-SHA256 / base64 signature over the raw request body."""
import base64
import hashlib
import hmac
import json
import logging
from datetime import timedelta
from fastapi import APIRouter, Request, Depends, HTTPException
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError
from .config import settings
from .db import get_db, SessionLocal
from .models import WebhookBatch, User, Change, LoginSession, PortalSetup, AdminAudit, now
from .wiw import WIW, WIWError
from .locations import lock_admin_changes
from .notifications import valid_email
from .roster import import_schedules, assigned_schedule_pair

router=APIRouter()
logger=logging.getLogger(__name__)


def payload_shape(value, depth=0):
    # Only structural field names are allowed; never log payload values or HR fields.
    if depth>4: return type(value).__name__
    if isinstance(value,dict):
        known={'events','data','payload','body','messages','type','userId','user_id','uuid','createdAt','sentAt','accountId','fields'}
        return {'kind':'object','fields':{key:payload_shape(item,depth+1) for key,item in value.items() if key in known and key!='fields'},
                'other_field_count':sum(key not in known for key in value)}
    if isinstance(value,list): return {'kind':'array','count':len(value),'first':payload_shape(value[0],depth+1) if value else None}
    return type(value).__name__


@router.post('/webhooks/wiw')
async def receive(request: Request,db=Depends(get_db)):
    cfg=settings()
    if not cfg.wiw_webhook_secret: raise HTTPException(503,'Webhook setup is not complete.')
    raw=b''
    async for chunk in request.stream():
        raw+=chunk
        if len(raw)>1024*1024: raise HTTPException(413,'Webhook is too large.')
    signature=base64.b64encode(hmac.new(cfg.wiw_webhook_secret.encode(),raw,hashlib.sha256).digest()).decode()
    if not hmac.compare_digest(signature.encode(),request.headers.get('X-Signed-Hmac-256','').encode()):
        raise HTTPException(403,'Invalid webhook signature.')
    if request.headers.get('X-Account-Id')!=str(cfg.wiw_account_id):
        raise HTTPException(403,'Unexpected WIW workplace.')
    payload=None
    reason='invalid_json'
    try:
        payload=json.loads(raw)
        reason='batch_shape'
        # Live WIW deliveries use an events envelope; callback examples show single events.
        events=payload['events'] if isinstance(payload,dict) and 'events' in payload else (payload if isinstance(payload,list) else [payload])
        if not isinstance(events,list) or not events or len(events)>1000: raise ValueError()
        ids=set()
        for event in events:
            reason='event_type_missing_or_invalid'
            if not isinstance(event,dict) or not isinstance(event.get('type'),str): raise ValueError()
            if event['type'] not in ('users::created','users::updated','users::deleted','users::invited'): continue
            reason='affected_user_id_missing_or_invalid'
            value=event.get('data',{}).get('userId')
            if isinstance(value,bool) or not str(value).isdigit() or int(value)<=0: raise ValueError()
            ids.add(int(value))
    except (ValueError,TypeError,AttributeError):
        logger.warning('WIW webhook rejected: reason=%s structure=%s',reason,json.dumps(payload_shape(payload),sort_keys=True))
        raise HTTPException(400,'Unexpected webhook event format.') from None
    if not ids: return {'accepted':True}
    key=hashlib.sha256(raw).hexdigest()
    if not db.get(WebhookBatch,key):
        db.add(WebhookBatch(digest=key,account_id=cfg.wiw_account_id,user_ids=sorted(ids)))
        try: db.commit()
        except IntegrityError: db.rollback() # Simultaneous redelivery already queued.
    return {'accepted':True}


def sync_employee(db,actor,api,uid,schedules):
    cfg=settings()
    record=api.call('GET',f'/2/users/{uid}').get('user')
    if (not isinstance(record,dict) or record.get('id')!=uid
            or record.get('account_id')!=cfg.wiw_account_id
            or type(record.get('activated')) is not bool or type(record.get('is_deleted')) is not bool):
        raise WIWError('Employee response did not match workplace or user.')
    person=db.scalar(select(User).where(User.wiw_user_id==uid).with_for_update())
    active=record['activated'] and not record['is_deleted']
    if not person and not active: return
    name=' '.join(str(record.get(k) or '').strip() for k in ('first_name','last_name')).strip()[:120] or f'Employee {uid}'
    address=record.get('email','')
    address=address.strip().lower() if isinstance(address,str) else ''
    pair=assigned_schedule_pair(record,schedules,person.location if person else '')
    routing_valid=pair is not None
    created=person is None
    if created:
        person=User(wiw_user_id=uid,email=f'wiw-{cfg.wiw_account_id}-{uid}@portal.invalid',
            name=name,password_hash='!',role='employee',active=True,location='',secondary_location='',
            notification_email=address if valid_email(address) else '')
        db.add(person);db.flush()
    before={'name':person.name,'active':person.active,'location':person.location,'secondary_location':person.secondary_location}
    person.name=name
    # Never reactivate a portal-disabled account automatically.
    if not active:
        person.active=False
        db.execute(delete(LoginSession).where(LoginSession.user_id==person.id))
        db.execute(delete(PortalSetup).where(PortalSetup.user_id==person.id))
    pending=db.scalar(select(Change.id).where(Change.employee_id==person.id,Change.status.in_(['pending','applying','needs_reconciliation'])))
    routing_review=not routing_valid or bool(pending)
    if routing_valid and not pending:
        person.location,person.secondary_location=pair
    db.add(AdminAudit(actor_id=actor.id,target_id=person.id,details={'event':'wiw_webhook_employee_sync',
        'created':created,'before':before,'after':{'name':person.name,'active':person.active,'location':person.location,'secondary_location':person.secondary_location},
        'location_review_required':routing_review}))


def process_webhooks(factory=SessionLocal,api_factory=WIW):
    cfg=settings()
    if cfg.wiw_mode!='live': return 0
    with factory() as db:
        batch=db.scalar(select(WebhookBatch).where(WebhookBatch.status.in_(['queued','retry']),WebhookBatch.next_attempt<=now()).order_by(WebhookBatch.created).limit(1).with_for_update(skip_locked=True))
        if not batch: return 0
        try:
            with db.begin_nested():
                if batch.account_id!=cfg.wiw_account_id: raise WIWError('Workplace configuration changed.')
                lock_admin_changes(db)
                actor=db.scalar(select(User).where(User.role=='admin',User.active.is_(True)).order_by(User.id))
                if not actor: raise WIWError('No active portal administrator.')
                api=api_factory()
                # Validate/import schedules once, retaining IDs for routing.
                records=api.call('GET','/2/locations')
                class ScheduleResponse:
                    def call(self,*args,**kwargs): return records
                import_schedules(db,actor,ScheduleResponse())
                schedules={r['id']:r['name'].strip() for r in records['locations'] if r.get('is_deleted') is False and not r.get('deleted_at')}
                for uid in batch.user_ids: sync_employee(db,actor,api,uid,schedules)
        except Exception as exc:
            batch.attempts+=1
            batch.status='failed' if batch.attempts>=8 else 'retry'
            batch.last_error=type(exc).__name__[:120]
            batch.next_attempt=now()+timedelta(minutes=min(60,2**batch.attempts))
        else:
            batch.status='done';batch.last_error=''
        db.commit()
        return 1
