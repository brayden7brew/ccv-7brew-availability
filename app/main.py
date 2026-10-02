import secrets
import re
import logging
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
from .models import ManagedEvent, User, Scope, Change, Audit, LoginSession, LoginAttempt, now
from .security import current_user, csrf, digest, login, verify, DUMMY
from .weekly import WeeklyInput, DAYS, timeline, timeline_ids, display_schedule, local_today
from .wiw import WIW, WIWError
from .wiw_auth import WIWAuth, WIWAuthError
from .workflow import allowed, audit, decide

logger = logging.getLogger(__name__)

cfg = settings()
app = FastAPI(title='CCV 7 Brew Portal', docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(DeviceSessionMiddleware, secret_key=cfg.secret_key, session_cookie='portal_session',
    normal_max_age=cfg.session_hours * 3600, same_site='lax', https_only=cfg.secure_cookies)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=cfg.allowed_hosts.split(','))
root = Path(__file__).parent
app.mount('/static', StaticFiles(directory=root/'static'), name='static')
templates = Jinja2Templates(directory=root/'templates')
from .weekly import week_totals, working_hour_labels
templates.env.globals['week_totals'] = week_totals
templates.env.globals['working_hour_labels'] = working_hour_labels
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
from .hub import router as hub_router
app.include_router(hub_router)

@app.middleware('http')
async def headers(request, call_next):
    response = await call_next(request)
    script_source = "'self'"
    response.headers.update({'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
        'Referrer-Policy': 'same-origin', 'Cache-Control': 'no-store',
        'Content-Security-Policy': f"default-src 'self'; style-src 'self'; script-src {script_source}; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
    if cfg.secure_cookies:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000'
    return response

def page(request, name, status_code=200, **context):
    request.session.setdefault('csrf', secrets.token_urlsafe(32))
    return templates.TemplateResponse(request=request, name=name, status_code=status_code, context={
        'csrf': request.session['csrf'], 'cfg': cfg, **context})

@app.exception_handler(HTTPException)
async def http_error(request, exc):
    if request.url.path.startswith('/ops/api/'):
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail': exc.detail}, status_code=exc.status_code)
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
        # A missing schedule stays unassigned until roster import/admin review;
        # never route approvals to the workplace name.
        # An unusable local password prevents creating a password fallback.
        user = User(wiw_user_id=wiw_user_id, email=f'wiw-{cfg.wiw_account_id}-{wiw_user_id}@portal.invalid',
            name=getattr(authenticator, 'display_name', '') or f'Employee {wiw_user_id}',
            password_hash='!', role='employee', active=True, location='')
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

@app.get('/requests')
def dashboard(request: Request, page_number: int = 1, db=Depends(get_db)):
    user = current_user(request, db)
    if page_number < 1 or page_number > 100000: raise HTTPException(422, 'Invalid page.')
    locations = list(db.scalars(select(Scope.location).where(Scope.manager_id == user.id))) if user.role in ('manager','admin') else []
    changes = db.scalars(select(Change).where(or_(Change.employee_id == user.id,
        Change.location.in_(locations))).order_by(Change.created.desc()).offset((page_number-1)*50).limit(50)).all()
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
    return page(request, 'availability.html', user=user, **availability_context(request, db, user))

def availability_context(request, db, user):
    rows = timeline(db, user.id, cfg.dry_run)
    today = local_today()
    current = next((r for r in reversed(rows) if r.effective_date <= today), None)
    upcoming = [r for r in rows if r.effective_date > today]
    from .availability_preview import preview
    try:
        selected = datetime.strptime(request.query_params.get('week', today.isoformat()), '%Y-%m-%d').date()
        if abs((selected-today).days) > 366: raise ValueError()
    except ValueError:
        raise HTTPException(422, 'Choose a week within one year of today.') from None
    week = selected - timedelta(days=selected.weekday())
    first, last = date_range(week.isoformat(), (week+timedelta(days=7)).isoformat())
    calendar = None
    try:
        state = WIW().read(user.wiw_user_id, first, last)
        calendar = preview(state['availabilityevents'], week)
    except (WIWError, ValueError, KeyError, TypeError, OverflowError):
        logger.warning('Availability calendar could not be loaded')
    return dict(days=DAYS,
        current=display_schedule(current), upcoming=[display_schedule(r) for r in upcoming],
        calendar=calendar, today=today, week=week, week_end=week+timedelta(days=6),
        previous_week=week-timedelta(days=7), next_week=week+timedelta(days=7),
        can_previous=(today-(week-timedelta(days=7))).days<=366,
        can_next=((week+timedelta(days=7))-today).days<=366)

@app.get('/requests/new')
def new(request: Request, db=Depends(get_db)):
    user = current_user(request, db)
    defaults = [{'mode':'none','start':'','end':''} for _ in DAYS]
    return weekly_form(request, db, user, defaults)


def weekly_form(request, db, user, defaults, *, effective_date='', employee_note='', error='', status_code=200):
    rules = employee_rules(db, user)
    return page(request, 'new.html', status_code=status_code, user=user, days=DAYS, defaults=defaults,
        usage=request_usage(db, user.id), rules=rules, earliest=rules['earliest'].isoformat(),
        latest=(local_today()+timedelta(days=366)).isoformat(), effective_date=effective_date,
        employee_note=employee_note, form_error=error)

@app.post('/requests')
async def submit(request: Request, db=Depends(get_db)):
    user = current_user(request, db)
    form = await request.form(max_fields=40)
    csrf(request, form.get('csrf'))
    if form.get('action') != 'weekly':
        raise HTTPException(422, 'Use the new weekly availability form.')
    note = str(form.get('employee_note', '')).strip()
    days = [{'mode':str(form.get(f'day_{i}_mode','none')), 'start':str(form.get(f'day_{i}_start','')),
             'end':str(form.get(f'day_{i}_end',''))} for i in range(7)]
    effective_date = str(form.get('effective_date', ''))

    def invalid(message, status_code=422):
        db.rollback()  # Release any employee lock and reload current requirements.
        return weekly_form(request, db, user, days, effective_date=effective_date,
            employee_note=note, error=message, status_code=status_code)

    if len(note) > 2000:
        return invalid('Keep your note to 2,000 characters or fewer.')
    try:
        data = WeeklyInput(effective_date=effective_date, days=days)
    except ValidationError as exc:
        return invalid('; '.join(e['msg'].removeprefix('Value error, ') for e in exc.errors()))
    allowed_times = {f'{m//60:02d}:{m%60:02d}' for m in range(300,1381,15)}
    for i, day in enumerate(data.days):
        if day.mode in ('hours', 'unavailable') and any(value not in allowed_times for value in (day.start,day.end)):
            return invalid(f'{DAYS[i]}: choose times between 5:00 AM and 11:00 PM in 15-minute steps.')
    # Serialize capture against approval of another schedule for this employee.
    db.scalar(select(User).where(User.id == user.id).with_for_update().execution_options(populate_existing=True))
    try:
        applied_rules=validate_employee_rules(db,user,data)
    except HTTPException as exc:
        return invalid(exc.detail, exc.status_code)
    if user.role in ('manager','admin') and db.scalar(select(Change.id).where(
            Change.employee_id == user.id, Change.status.in_(['applying','needs_reconciliation']))):
        return invalid('An earlier availability update needs attention. Ask your administrator to resolve it before saving another update.', 409)
    usage = request_usage(db, user.id)
    if usage['blocked']:
        return invalid(f"You have reached the limit of {usage['limit']} requests in 30 days. You can submit again at {local_datetime(usage['reset'])}.", 429)
    rows = timeline(db, user.id, cfg.dry_run)
    zone = ZoneInfo(cfg.business_timezone)
    begin = datetime.combine(local_today(), datetime.min.time(), tzinfo=zone)
    end = datetime.combine(data.effective_date+timedelta(days=366), datetime.min.time(), tzinfo=zone)
    first, last = begin.isoformat(), end.isoformat()
    try:
        before = WIW().read(user.wiw_user_id, first, last)
    except WIWError as exc:
        reference = secrets.token_hex(6)
        logger.warning('WIW availability read failed: reference=%s reason=%s http_status=%s wiw_code=%s',
            reference, exc.reason, exc.http_status, exc.wiw_code)
        if exc.http_status in (401,403):
            explanation = 'The portal could not access When I Work. Ask your administrator to check the connection.'
        elif exc.reason == 'availability_changed':
            explanation = 'Your When I Work availability changed while it was loading. Please try again.'
        elif exc.reason in ('timeout','connection_error') or exc.http_status == 429 or (exc.http_status and exc.http_status >= 500):
            explanation = 'When I Work could not be reached right now. Please try again later.'
        else:
            explanation = 'We could not load your current availability from When I Work. Ask your administrator to check the connection.'
        return invalid(f'{explanation} Your request has not been submitted. Your entries are kept below. Reference: {reference}.', 503)
    prior = next((r for r in reversed(rows) if r.effective_date <= data.effective_date), None)
    before.update(timeline_ids=timeline_ids(rows), schedule=display_schedule(prior), dry_run=cfg.dry_run)
    change = Change(employee_id=user.id, location=user.location, secondary_location=user.secondary_location, wiw_user_id=user.wiw_user_id,
        action='weekly', proposed=data.model_dump(mode='json'), before=before,
        read_start=first, read_end=last, employee_note=note)
    db.add(change)
    db.flush()
    user.first_request_notice_exception=False
    direct = user.role in ('manager', 'admin')
    audit(db, change, user, 'submitted_direct' if direct else 'submitted', {'before':before, 'proposed':change.proposed, 'action':'weekly','rules':applied_rules})
    db.commit()
    if direct:
        await run_in_threadpool(decide, db, user, change.id, 'approve', '', WIW())
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
    managed_ids = set(db.scalars(select(ManagedEvent.event_id).where(
        ManagedEvent.employee_id == change.employee_id, ManagedEvent.active.is_(True))).all())
    existing_events = [e for e in change.before.get('availabilityevents', []) if e['id'] not in managed_ids]
    return page(request, 'detail.html', user=user, change=change, employee=employee, history=history, existing_events=existing_events, actor_names=actor_names, days=DAYS, usage=request_usage(db, employee.id))

@app.post('/requests/{change_id}/decision')
async def decision(request: Request, change_id: int, db=Depends(get_db)):
    user = current_user(request, db)
    form = await request.form(max_fields=10)
    csrf(request, form.get('csrf'))
    await run_in_threadpool(decide, db, user, change_id, form.get('decision'), str(form.get('note','')).strip(), replace_existing=form.get('replace_existing') == 'yes')
    return RedirectResponse(f'/requests/{change_id}', 303)
