import secrets
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select, func, or_, text
from starlette.concurrency import run_in_threadpool
from .sessions import DeviceSessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .config import settings
from .request_limits import request_usage
from .availability_rules import employee_rules, validate_employee_rules
from .db import get_db
from .models import User, Scope, Change, Audit, LoginSession, LoginAttempt, now
from .security import current_user, csrf, digest, login, verify, DUMMY
from .weekly import WeeklyInput, DAYS, timeline, timeline_ids, display_schedule, local_today
from .wiw import WIW, WIWError
from .wiw_auth import WIWAuth, WIWAuthError
from .workflow import allowed, audit, decide

cfg = settings()
app = FastAPI(title='CCV 7 Brew Availability', docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(DeviceSessionMiddleware, secret_key=cfg.secret_key, session_cookie='portal_session',
    normal_max_age=cfg.session_hours * 3600, same_site='lax', https_only=cfg.secure_cookies)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=cfg.allowed_hosts.split(','))
root = Path(__file__).parent
app.mount('/static', StaticFiles(directory=root/'static'), name='static')
templates = Jinja2Templates(directory=root/'templates')
from .weekly import week_totals
templates.env.globals['week_totals'] = week_totals
from .availability_rules import counted_minutes
from .presentation import clock_label, date_label, local_datetime
templates.env.globals['counted_minutes'] = counted_minutes
templates.env.filters.update(clock_label=clock_label, date_label=date_label, local_datetime=local_datetime)
from .admin import router as admin_router
app.include_router(admin_router)
from .portal_setup import router as portal_setup_router
app.include_router(portal_setup_router)
from .webhooks import router as webhook_router
app.include_router(webhook_router)

@app.middleware('http')
async def headers(request, call_next):
    response = await call_next(request)
    response.headers.update({'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
        'Referrer-Policy': 'same-origin', 'Cache-Control': 'no-store',
        'Content-Security-Policy': "default-src 'self'; style-src 'self'; script-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
    if cfg.secure_cookies:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000'
    return response

def page(request, name, **context):
    request.session.setdefault('csrf', secrets.token_urlsafe(32))
    return templates.TemplateResponse(request=request, name=name, context={
        'csrf': request.session['csrf'], 'cfg': cfg, **context})

@app.exception_handler(HTTPException)
async def http_error(request, exc):
    if exc.status_code == 401:
        return RedirectResponse('/login', status_code=303)
    return templates.TemplateResponse(request=request, name='error.html',
        context={'message': exc.detail, 'cfg': cfg}, status_code=exc.status_code)

@app.exception_handler(WIWError)
async def wiw_error(request, exc):
    return templates.TemplateResponse(request=request, name='error.html',
        context={'message': str(exc), 'cfg': cfg}, status_code=503)

@app.get('/healthz')
def health(db=Depends(get_db)):
    db.execute(text('SELECT 1'))
    return {'status': 'ok'}

def limit_login(request, db, email):
    keys = [digest('email:' + email), digest('ip:' + (request.client.host if request.client else 'unknown'))]
    cutoff = now() - timedelta(minutes=15)
    for key in keys:
        count = db.scalar(select(func.count()).select_from(LoginAttempt).where(LoginAttempt.key == key, LoginAttempt.created > cutoff))
        if count >= 10:
            raise HTTPException(429, 'Too many sign-in attempts. Try again in 15 minutes.')
    for key in keys: db.add(LoginAttempt(key=key))
    db.commit()

@app.get('/login/wiw')
def wiw_login_page(request: Request):
    return page(request, 'login.html', wiw_login=True)

@app.post('/login/wiw')
async def wiw_login_post(request: Request, db=Depends(get_db)):
    form = await request.form(max_fields=10)
    csrf(request, form.get('csrf'))
    remember = form.get('remember') == 'on'
    email = str(form.get('email','')).strip().lower()
    password = str(form.get('password',''))
    if not email or not password or len(email)>254 or len(password)>1024:
        raise HTTPException(422, 'Enter your WIW email and password.')
    limit_login(request, db, email)
    try:
        authenticator = WIWAuth()
        wiw_user_id = await run_in_threadpool(authenticator.authenticate, email, password)
    except WIWAuthError as exc:
        return page(request, 'login.html', wiw_login=True, error=str(exc))
    finally:
        # Never persist passwords or WIW tokens in the database/session/audit.
        password = None
        form = None
    user = db.scalar(select(User).where(User.wiw_user_id == wiw_user_id))
    if user is None and cfg.wiw_auto_enroll:
        # Identity is matched only by verified workplace user ID, never email.
        # A random, unusable local password prevents creating a password fallback.
        user = User(wiw_user_id=wiw_user_id, email=f'wiw-{cfg.wiw_account_id}-{wiw_user_id}@portal.invalid',
            name=getattr(authenticator, 'display_name', '') or f'Employee {wiw_user_id}',
            password_hash='!', role='employee', active=True, location=cfg.wiw_auto_enroll_location)
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            user = db.scalar(select(User).where(User.wiw_user_id == wiw_user_id))
    if not user or not user.active:
        return page(request, 'login.html', wiw_login=True,
            error='Your WIW login is valid, but portal access has not been enabled. Contact your manager.')
    from .notifications import valid_email
    if not user.notification_email and valid_email(email):
        user.notification_email = email
        db.commit()
    login(request, db, user, remember=remember)
    return RedirectResponse('/',303)

@app.get('/login')
def login_page(request: Request):
    return page(request, 'login.html', wiw_login=bool(cfg.wiw_developer_key))

@app.get('/login/portal')
def portal_login_page(request: Request):
    return page(request, 'login.html', wiw_login=False)

@app.post('/login')
async def login_post(request: Request, db=Depends(get_db)):
    form = await request.form(max_fields=10)
    csrf(request, form.get('csrf'))
    remember = form.get('remember') == 'on'
    email, password = str(form.get('email', '')).strip().lower(), str(form.get('password', ''))
    if len(email) > 254 or len(password) > 1024:
        raise HTTPException(422, 'Invalid login input.')
    limit_login(request, db, email)
    user = db.scalar(select(User).where(User.email == email))
    valid = verify(password, user.password_hash if user else DUMMY)
    if not user or not user.active or not valid:
        return page(request, 'login.html', error='Email or password is incorrect.')
    login(request, db, user, remember=remember)
    return RedirectResponse('/', 303)

@app.post('/logout')
async def logout(request: Request, db=Depends(get_db)):
    form = await request.form(max_fields=10)
    csrf(request, form.get('csrf'))
    session = db.get(LoginSession, digest(request.session.get('sid', '')))
    if session: db.delete(session)
    db.commit()
    request.session.clear()
    return RedirectResponse('/login', 303)

@app.get('/')
def dashboard(request: Request, page_number: int = 1, db=Depends(get_db)):
    user = current_user(request, db)
    if page_number < 1 or page_number > 100000: raise HTTPException(422, 'Invalid page.')
    locations = list(db.scalars(select(Scope.location).where(Scope.manager_id == user.id))) if user.role in ('manager','admin') else []
    changes = db.scalars(select(Change).where(or_(Change.employee_id == user.id,
        Change.location.in_(locations), Change.secondary_location.in_(locations))).order_by(Change.created.desc()).offset((page_number-1)*50).limit(50)).all()
    names = {u.id: u.name for u in db.scalars(select(User).where(User.id.in_({c.employee_id for c in changes}))).all()}
    return page(request, 'dashboard.html', user=user, changes=changes, names=names, page_number=page_number, usage=request_usage(db, user.id),
        employee_usage={i:request_usage(db, i) for i in names})

def date_range(start=None, end=None):
    zone = ZoneInfo(cfg.business_timezone)
    try:
        first = datetime.fromisoformat(start).replace(tzinfo=zone) if start else datetime.now(zone).replace(hour=0,minute=0,second=0,microsecond=0)
        last = datetime.fromisoformat(end).replace(tzinfo=zone) if end else first + timedelta(days=90)
        if not timedelta(0) < last-first <= timedelta(days=366): raise ValueError()
    except ValueError:
        raise HTTPException(422, 'Choose a date range between 1 and 366 days.')
    return first.isoformat(), last.isoformat()

@app.get('/availability')
def availability(request: Request, db=Depends(get_db)):
    user = current_user(request, db)
    rows = timeline(db, user.id, cfg.dry_run)
    today = local_today()
    current = next((r for r in reversed(rows) if r.effective_date <= today), None)
    upcoming = [r for r in rows if r.effective_date > today]
    first, last = date_range()
    state = WIW().read(user.wiw_user_id, first, last)
    return page(request, 'availability.html', user=user, days=DAYS,
        current=display_schedule(current), upcoming=[display_schedule(r) for r in upcoming],
        events=state['availabilityevents'])

@app.get('/requests/new')
def new(request: Request, db=Depends(get_db)):
    user = current_user(request, db)
    rows = timeline(db, user.id, cfg.dry_run)
    defaults = rows[-1].days if rows else [{'mode':'none','start':'','end':''} for _ in DAYS]
    return page(request, 'new.html', user=user, days=DAYS, defaults=defaults,
        usage=request_usage(db, user.id), rules=employee_rules(db,user), earliest=employee_rules(db,user)['earliest'].isoformat())

@app.post('/requests')
async def submit(request: Request, db=Depends(get_db)):
    user = current_user(request, db)
    form = await request.form(max_fields=40)
    csrf(request, form.get('csrf'))
    if form.get('action') != 'weekly':
        raise HTTPException(422, 'Use the new weekly availability form.')
    note = str(form.get('employee_note', '')).strip()
    if len(note) > 2000: raise HTTPException(422, 'Reason is limited to 2000 characters.')
    try:
        data = WeeklyInput(effective_date=form.get('effective_date'), days=[{
            'mode':form.get(f'day_{i}_mode','none'), 'start':form.get(f'day_{i}_start',''),
            'end':form.get(f'day_{i}_end','')} for i in range(7)])
    except ValidationError as exc:
        raise HTTPException(422, '; '.join(e['msg'] for e in exc.errors()))
    # Serialize capture against approval of another schedule for this employee.
    db.scalar(select(User).where(User.id == user.id).with_for_update().execution_options(populate_existing=True))
    applied_rules=validate_employee_rules(db,user,data)
    usage = request_usage(db, user.id)
    if usage['blocked']:
        raise HTTPException(429, f"You have reached the limit of {usage['limit']} requests in 30 days. You can submit again at {usage['reset'].isoformat()}.")
    rows = timeline(db, user.id, cfg.dry_run)
    zone = ZoneInfo(cfg.business_timezone)
    begin = datetime.combine(local_today(), datetime.min.time(), tzinfo=zone)
    end = datetime.combine(data.effective_date+timedelta(days=366), datetime.min.time(), tzinfo=zone)
    first, last = begin.isoformat(), end.isoformat()
    before = WIW().read(user.wiw_user_id, first, last)
    prior = next((r for r in reversed(rows) if r.effective_date <= data.effective_date), None)
    before.update(timeline_ids=timeline_ids(rows), schedule=display_schedule(prior), dry_run=cfg.dry_run)
    change = Change(employee_id=user.id, location=user.location, secondary_location=user.secondary_location, wiw_user_id=user.wiw_user_id,
        action='weekly', proposed=data.model_dump(mode='json'), before=before,
        read_start=first, read_end=last, employee_note=note)
    db.add(change)
    db.flush()
    user.first_request_notice_exception=False
    audit(db, change, user, 'submitted', {'before':before, 'proposed':change.proposed, 'action':'weekly','rules':applied_rules})
    db.commit()
    return RedirectResponse(f'/requests/{change.id}', 303)

@app.get('/requests/{change_id}')
def detail(request: Request, change_id: int, db=Depends(get_db)):
    user = current_user(request, db)
    change = db.get(Change, change_id)
    if not change or not allowed(db, user, change): raise HTTPException(404, 'Request not found.')
    employee = db.get(User, change.employee_id)
    history = db.scalars(select(Audit).where(Audit.change_id == change.id).order_by(Audit.id)).all()
    actor_ids = {entry.actor_id for entry in history if entry.actor_id}
    actor_names = dict(db.execute(select(User.id, User.name).where(User.id.in_(actor_ids))).all())
    return page(request, 'detail.html', user=user, change=change, employee=employee, history=history, actor_names=actor_names, days=DAYS, usage=request_usage(db, employee.id))

@app.post('/requests/{change_id}/decision')
async def decision(request: Request, change_id: int, db=Depends(get_db)):
    user = current_user(request, db)
    form = await request.form(max_fields=10)
    csrf(request, form.get('csrf'))
    await run_in_threadpool(decide, db, user, change_id, form.get('decision'), str(form.get('note','')).strip())
    return RedirectResponse(f'/requests/{change_id}', 303)
