"""Stand-scoped time off with durable approval reservations and blackout checks."""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from dateutil.parser import parse
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select, or_
from .db import get_db, SessionLocal
from .models import User, Scope, Change, Audit, Blackout, TimeOffSettings, AdminAudit
from .security import current_user, csrf
from .config import settings
from .weekly import local_today
from .workflow import audit
from .wiw import WIW, WIWError
from .time_off_wiw import read_requests, unpaid_type, verify

router=APIRouter()
PERIODS={'all_day':(5,23,'Whole day · 5 AM–close'),'morning':(5,12,'Morning · 5 AM–noon'),'afternoon':(12,17,'Afternoon · noon–5 PM'),
         'evening':(17,23,'Evening · 5 PM–close')}
RESERVED=('queued','preparing','applying','needs_reconciliation','applied','reconciled_applied','approved_dry_run')


def feature(db,lock=False):
    query=select(TimeOffSettings).where(TimeOffSettings.id==1)
    return db.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)


def enabled(db,user):
    state=feature(db)
    return bool(state and state.enabled and user.active and (state.everyone or user.time_off_access))


def actor_for(request,db):
    user=current_user(request,db)
    if not enabled(db,user): raise HTTPException(403,'Time off is not enabled for your account.')
    return user


def stands(db,user):
    if user.role not in ('admin','manager'): return []
    return list(db.scalars(select(Scope.location).where(Scope.manager_id==user.id)))


def blackout_stands(db,user):
    if user.role=='admin':
        from .locations import location_choices
        return [name for name in location_choices(db) if name]
    return stands(db,user)


def can_review(db,user,change):
    return (user.role in ('manager','admin') and change.location in stands(db,user)
            and (user.id!=change.employee_id or user.role=='admin'))


def interval(proposed):
    return parse(proposed['start']),parse(proposed['end'])


def overlaps(a,b,c,d): return a<d and b>c


def parse_form(form):
    zone=ZoneInfo(settings().business_timezone)
    try:
        first=date.fromisoformat(str(form.get('first','')));last=date.fromisoformat(str(form.get('last') or first.isoformat()))
        if first<local_today()+timedelta(days=14): raise ValueError('Time off requires at least 14 days’ notice.')
        if last<first or (last-first).days>30: raise ValueError('Choose a date range of up to 31 days.')
        full=form.get('full_day')=='on'
        if full:
            start=datetime.combine(first,time(),zone);end=datetime.combine(last+timedelta(days=1),time(),zone)
        else:
            if last!=first: raise ValueError('For partial days, select the same start and end date.')
            start=datetime.combine(first,time.fromisoformat(str(form.get('from',''))),zone)
            end=datetime.combine(first,time.fromisoformat(str(form.get('to',''))),zone)
            if start>=end: raise ValueError('End time must be after start time.')
        reason=str(form.get('reason','')).strip()
        if not reason or len(reason)>2000: raise ValueError('Enter a reason of up to 2,000 characters.')
    except (ValueError,TypeError) as exc:
        raise HTTPException(422,str(exc) or 'Check the requested dates and hours.') from None
    return {'start':start.isoformat(),'end':end.isoformat(),'full_day':full},reason


def closing_hour(day):
    return 23 if day.weekday() in (4,5) else 22


def blackout_label(blackout):
    label=PERIODS[blackout.period][2]
    return label.replace('close',f'{closing_hour(blackout.day)-12} PM')


def blackout_interval(blackout):
    zone=ZoneInfo(settings().business_timezone)
    begin,finish,_=PERIODS[blackout.period]
    if blackout.period in ('all_day','evening'): finish=closing_hour(blackout.day)
    a=datetime.combine(blackout.day,time(begin),zone)
    return a,a.replace(hour=finish)


def blocked_people(db,api,blackout,external,cache):
    """Count distinct active primary-stand people with any unavailable overlap."""
    a,b=blackout_interval(blackout)
    people=list(db.scalars(select(User).where(User.active.is_(True),User.location==blackout.location)))
    ids={p.id for p in people};wid={p.wiw_user_id:p.id for p in people};blocked=set()
    from .availability_preview import preview
    for person in people:
        key=(person.id,blackout.day)
        if key not in cache:
            events=api.read(person.wiw_user_id,a.replace(hour=0).isoformat(),
                            (a.replace(hour=0)+timedelta(days=1)).isoformat())['availabilityevents']
            cache[key]=preview(events,blackout.day)[0]['windows']
        # Preview windows are percentages of the 18-hour working period.
        available=0
        for win in cache[key]:
            lo=a.replace(hour=5)+timedelta(seconds=win['top']*648)
            hi=lo+timedelta(seconds=win['height']*648)
            available+=max(0,(min(b,hi)-max(a,lo)).total_seconds())
        if available<(b-a).total_seconds()-0.01: blocked.add(person.id)
    for item in external:
        if item['user_id'] not in wid or item.get('user_status',item.get('status'))!=2: continue
        try:
            lo,hi=parse(item['start_time']),parse(item['end_time'])
            if lo.tzinfo is None or hi.tzinfo is None or hi<=lo: raise ValueError()
            if overlaps(a,b,lo,hi): blocked.add(wid[item['user_id']])
        except (KeyError,ValueError,TypeError): raise WIWError('Could not verify existing time-off dates.') from None
    for change in db.scalars(select(Change).where(Change.action=='time_off',Change.employee_id.in_(ids),
                            Change.status.in_(RESERVED))):
        if change.dry_run is not None and change.dry_run!=settings().dry_run: continue
        lo,hi=interval(change.proposed)
        if overlaps(a,b,lo,hi): blocked.add(change.employee_id)
    return blocked,a,b


def capacity(db,api,user,proposed,exclude=None):
    start,end=interval(proposed)
    blackouts=list(db.scalars(select(Blackout).where(Blackout.location==user.location,
        Blackout.day>=start.date(),Blackout.day<=end.date()).order_by(Blackout.day,Blackout.period)))
    blackouts=[row for row in blackouts if overlaps(start,end,*blackout_interval(row))]
    external=read_requests(api,start.date(),end.date()) if blackouts else []
    cache={};result=[]
    for row in blackouts:
        people,a,b=blocked_people(db,api,row,external,cache)
        if not overlaps(start,end,a,b): continue
        # A person already unavailable still cannot submit once the cap is full.
        # Worker recheck excludes its own reservation, but retains normal WIW absence.
        if exclude:
            others=db.scalars(select(Change).where(Change.action=='time_off',Change.employee_id==user.id,
                Change.id!=exclude,Change.status.in_(RESERVED))).all()
            people.discard(user.id)
            # Capacity is assessed for adding this person, counted only once.
            if any(overlaps(a,b,*interval(c.proposed)) for c in others): people.add(user.id)
        full=len(people)>=row.maximum if not exclude else len(people|{user.id})>row.maximum
        result.append({'day':row.day,'period':blackout_label(row),'count':len(people),
                       'maximum':row.maximum,'explanation':row.explanation,'full':full})
    return result


def check_capacity(db,api,user,proposed,exclude=None):
    rows=capacity(db,api,user,proposed,exclude)
    blocked=[r for r in rows if r['full']]
    if blocked:
        r=blocked[0]
        raise HTTPException(409,f"{r['day']}: {r['period']} is full ({r['maximum']} allowed). {r['explanation']}")
    return rows


def blackout_request_counts(db,blackouts):
    """Portal request totals are distinct from people consuming blackout capacity."""
    locations={row.location for row in blackouts}
    changes=db.scalars(select(Change).join(User,User.id==Change.employee_id).where(
        Change.action=='time_off',Change.location.in_(locations),User.active.is_(True),
        User.location==Change.location,
        ~select(Audit.id).where(Audit.change_id==Change.id,Audit.event=='employee_archived').exists()
    )).all() if locations else []
    counts={}
    for row in blackouts:
        start,end=blackout_interval(row)
        matching=[change for change in changes if change.location==row.location
                  and (change.dry_run is None or change.dry_run==settings().dry_run)
                  and overlaps(start,end,*interval(change.proposed))]
        counts[row.id]={'requests':len(matching),
                        'approved':sum(change.status in RESERVED for change in matching)}
    return counts


@router.get('/time-off')
def index(request:Request,db=Depends(get_db)):
    from .main import page
    user=actor_for(request,db);locations=stands(db,user)
    manage_locations=blackout_stands(db,user)
    rows=db.scalars(select(Change).where(Change.action=='time_off',
        or_(Change.employee_id==user.id,Change.location.in_(locations)),
        ~select(Audit.id).where(Audit.change_id==Change.id,Audit.event=='employee_archived').exists()
        ).order_by(Change.created.desc()).limit(100)).all()
    names=dict(db.execute(select(User.id,User.name).where(User.id.in_({c.employee_id for c in rows}))).all())
    blackouts=db.scalars(select(Blackout).where(Blackout.location.in_(manage_locations),
                       Blackout.day>=local_today()).order_by(Blackout.day,Blackout.location,Blackout.period)).all() if manage_locations else []
    blackout_counts=blackout_request_counts(db,blackouts)
    return page(request,'time_off.html',user=user,rows=rows,names=names,blackouts=blackouts,
        locations=locations,manage_locations=manage_locations,periods=PERIODS,blackout_label=blackout_label,blackout_counts=blackout_counts,earliest=local_today()+timedelta(days=14),
        processing=any(c.status in ('queued','preparing','applying') for c in rows))


@router.post('/time-off')
async def submit(request:Request,db=Depends(get_db)):
    user=actor_for(request,db);form=await request.form(max_fields=12);csrf(request,form.get('csrf'))
    proposed,reason=parse_form(form)
    feature(db,True) # Serialize submissions/approvals/blackout edits across stands.
    db.refresh(user)
    if not enabled(db,user): raise HTTPException(403,'Time off is not enabled.')
    user=db.scalar(select(User).where(User.id==user.id).with_for_update())
    a,b=interval(proposed)
    for other in db.scalars(select(Change).where(Change.employee_id==user.id,Change.action=='time_off',
                            Change.status.in_(('pending',)+RESERVED))):
        if overlaps(a,b,*interval(other.proposed)): raise HTTPException(409,'You already have a request covering these hours.')
    check_capacity(db,WIW(),user,proposed)
    change=Change(employee_id=user.id,location=user.location,secondary_location=user.secondary_location,
        wiw_user_id=user.wiw_user_id,action='time_off',proposed=proposed,before={},
        read_start=proposed['start'],read_end=proposed['end'],employee_note=reason)
    db.add(change);db.flush();audit(db,change,user,'submitted',{})
    db.commit();return RedirectResponse('/time-off',303)


@router.get('/time-off/{change_id:int}')
def detail(request:Request,change_id:int,db=Depends(get_db)):
    from .main import page
    user=actor_for(request,db);change=db.get(Change,change_id)
    if (not change or change.action!='time_off' or
        db.scalar(select(Audit.id).where(Audit.change_id==change_id,Audit.event=='employee_archived')) or
        (user.id!=change.employee_id and not can_review(db,user,change))):
        raise HTTPException(404,'Request not found.')
    history=db.scalars(select(Audit).where(Audit.change_id==change.id).order_by(Audit.id)).all() if user.role=='admin' else []
    return page(request,'time_off_detail.html',user=user,change=change,employee=db.get(User,change.employee_id),
                review=can_review(db,user,change),history=history)


@router.post('/time-off/{change_id:int}/decision')
async def decision(request:Request,change_id:int,db=Depends(get_db)):
    user=actor_for(request,db);form=await request.form(max_fields=5);csrf(request,form.get('csrf'))
    feature(db,True);db.refresh(user)
    change=db.scalar(select(Change).where(Change.id==change_id).with_for_update())
    if not enabled(db,user) or not change or change.action!='time_off' or not can_review(db,user,change):
        raise HTTPException(403,'You cannot decide this request.')
    if change.status!='pending': return RedirectResponse('/time-off',303)
    person=db.get(User,change.employee_id)
    if not person.active or person.location!=change.location or person.wiw_user_id!=change.wiw_user_id:
        raise HTTPException(409,'Employee access or primary stand changed.')
    action=form.get('decision');note=str(form.get('note','')).strip()
    if action not in ('approve','reject') or len(note)>2000: raise HTTPException(422,'Choose approve or decline.')
    change.manager_id=user.id;change.manager_note=note
    if action=='reject':
        change.status='rejected';audit(db,change,user,'rejected',{'note':note})
    else:
        if interval(change.proposed)[0].date()<=local_today(): raise HTTPException(409,'The requested date has passed.')
        check_capacity(db,WIW(),person,change.proposed)
        change.status='queued';change.dry_run=settings().dry_run
        audit(db,change,user,'approval_queued',{'note':note})
    db.commit();return RedirectResponse('/time-off',303)


@router.post('/time-off/blackouts')
async def save_blackout(request:Request,db=Depends(get_db)):
    user=actor_for(request,db);form=await request.form(max_fields=100);csrf(request,form.get('csrf'))
    feature(db,True);db.refresh(user)
    period=str(form.get('period','all_day'))
    allowed=blackout_stands(db,user)
    if form.get('all_stands')=='on':
        if user.role!='admin': raise HTTPException(403,'Only administrators can choose all stands.')
        selected=allowed
    else:
        selected=sorted(set(str(value) for value in form.getlist('locations'))) or [str(form.get('location',''))]
    if not enabled(db,user) or not selected or any(location not in allowed for location in selected):
        raise HTTPException(403,'Choose a stand you manage.')
    try:
        day=date.fromisoformat(str(form.get('day','')));maximum=int(str(form.get('maximum','')))
        closing=closing_hour(day);explanation=str(form.get('explanation','')).strip()
        if day<local_today() or period not in PERIODS or not 0<=maximum<=1000 or closing not in (22,23): raise ValueError()
        if not explanation or len(explanation)>500: raise ValueError()
    except ValueError: raise HTTPException(422,'Choose a date, period, limit, and explanation (up to 500 characters).') from None
    for location in selected:
        row=db.scalar(select(Blackout).where(Blackout.location==location,Blackout.day==day,Blackout.period==period))
        if form.get('remove')=='yes':
            if row: db.delete(row)
        else:
            if row is None: row=Blackout(location=location,day=day,period=period);db.add(row)
            row.maximum=maximum;row.explanation=explanation;row.evening_end=closing
        db.add(AdminAudit(actor_id=user.id,target_id=user.id,details={'event':'time_off_blackout',
            'location':location,'day':day.isoformat(),'period':period,'maximum':maximum,'remove':form.get('remove')=='yes'}))
    db.commit();return RedirectResponse('/time-off',303)


@router.post('/admin/time-off')
async def access(request:Request,db=Depends(get_db)):
    from .admin import require_admin
    user=require_admin(request,db);form=await request.form(max_fields=5);csrf(request,form.get('csrf'))
    state=feature(db,True)
    db.refresh(user)
    if not user.active or user.role!='admin': raise HTTPException(403,'Administrator access is required.')
    if not state: raise HTTPException(503,'Run the time-off database migration first.')
    state.enabled=form.get('enabled')=='on';state.everyone=form.get('everyone')=='on'
    db.add(AdminAudit(actor_id=user.id,target_id=user.id,details={'event':'time_off_access',
        'enabled':state.enabled,'everyone':state.everyone}))
    db.commit();return RedirectResponse('/admin',303)


def process_time_off(factory=SessionLocal,api_factory=WIW):
    with factory() as db:
        state=db.scalar(select(TimeOffSettings).where(TimeOffSettings.id==1).with_for_update(skip_locked=True))
        if not state or not state.enabled: return 0
        change=db.scalar(select(Change).where(Change.action=='time_off',Change.status=='queued')
                         .order_by(Change.id).with_for_update(skip_locked=True).limit(1))
        if not change: return 0
        actor=db.get(User,change.manager_id);person=db.get(User,change.employee_id)
        try:
            if not actor or not enabled(db,actor) or not can_review(db,actor,change):
                raise HTTPException(403,'Manager access changed before processing.')
            if (not person.active or person.location!=change.location or person.wiw_user_id!=change.wiw_user_id
                or change.dry_run!=settings().dry_run): raise HTTPException(409,'Employee mapping or mode changed.')
            api=api_factory();check_capacity(db,api,person,change.proposed,exclude=change.id)
            a,b=interval(change.proposed)
            if a.date()<=local_today(): raise HTTPException(409,'The requested date has passed.')
            if change.dry_run:
                change.status='approved_dry_run';audit(db,change,actor,'approved',{});db.commit();return 1
            if settings().wiw_mode!='live': raise HTTPException(409,'Live WIW mode is required.')
            payload={'user_id':change.wiw_user_id,'account_id':settings().wiw_account_id,
                'start_time':a.isoformat(),'end_time':(b-timedelta(seconds=1) if change.proposed['full_day'] else b).isoformat(),
                'type_id':unpaid_type(api),'paid':False,'status':2,'user_status':2}
            change.status='applying';audit(db,change,actor,'operation_started',{'payload':payload})
            db.commit() # Durable reservation; a crashed worker must not repeat this POST.
            response=api.call('POST','/2/requests',payload=payload)
            record=verify(response.get('request'),payload)
            audit(db,change,actor,'time_off_created',{'id':record['id']});db.commit()
            current=api.call('GET',f"/2/requests/{record['id']}").get('request')
            verify(current,payload)
            if current['id']!=record['id']:
                raise WIWError('WIW returned a different time-off record during verification.')
            change.event_id=record['id'];change.status='applied'
            audit(db,change,actor,'approved',{});audit(db,change,actor,'applied',{'id':record['id']});db.commit()
        except Exception as exc:
            db.rollback();db.refresh(change)
            uncertain=change.status=='applying'
            change.status='needs_reconciliation' if uncertain else 'approval_failed'
            message=str(exc.detail) if isinstance(exc,HTTPException) else str(exc) if isinstance(exc,WIWError) else 'Time off could not be verified. Administrator review is required.'
            audit(db,change,actor,'write_uncertain' if uncertain else 'approval_failed',{'message':message})
            db.commit()
        return 1
