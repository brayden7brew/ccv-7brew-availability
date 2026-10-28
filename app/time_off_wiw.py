"""WIW time off uses /2/requests, never availabilityevents.
Contract: https://apidocs.wheniwork.com/external/index.html (Requests).
No write is retried automatically, including a successful POST with failed readback.
"""
from datetime import timedelta
from dateutil.parser import parse
from .config import settings
from .wiw import WIWError


def read_requests(api, first, last):
    if settings().wiw_mode=='demo': return []
    found={}; cursor=first-timedelta(days=1)
    while cursor<=last:
        end=min(last+timedelta(days=1),cursor+timedelta(days=27)); seen=set()
        for page in range(100):
            body=api.call('GET','/2/requests',params={'start':cursor.isoformat(),'end':end.isoformat(),
                'page':page,'limit':200,'include_deleted_users':'true','sortby':'created'})
            rows=body.get('requests'); more=body.get('more')
            if not isinstance(rows,list) or (more is not None and type(more) is not bool):
                raise WIWError('Could not verify current time off.')
            for row in rows:
                if (not isinstance(row,dict) or type(row.get('id')) is not int
                    or row.get('account_id')!=settings().wiw_account_id or type(row.get('user_id')) is not int):
                    raise WIWError('Time off belongs to an unexpected account or user.')
            signature=tuple(r['id'] for r in rows)
            if signature and signature in seen: raise WIWError('Incomplete time-off pagination.')
            seen.add(signature);found.update({r['id']:r for r in rows})
            if more is False or (more is None and len(rows)<200): break
            if not rows: raise WIWError('Incomplete time-off pagination.')
        else: raise WIWError('Too many time-off pages to verify safely.')
        cursor=end
    return list(found.values())


def unpaid_type(api):
    rows=api.call('GET','/2/requesttypes').get('request-types')
    if not isinstance(rows,list): raise WIWError('Could not read WIW time-off types.')
    matches=[r for r in rows if isinstance(r,dict) and
             str(r.get('name','')).strip().lower()=='hourly employee time off'
             and r.get('allow_unpaid') is True and r.get('enabled',True) is True and not r.get('is_deleted')]
    if len(matches)!=1 or type(matches[0].get('id')) is not int:
        raise WIWError('Configure one enabled unpaid WIW type named Hourly Employee Time Off before approving.')
    return matches[0]['id']


def verify(record,payload):
    try:
        valid=(type(record.get('id')) is int and record['id']>0 and
            all(record.get(k)==payload[k] for k in ('account_id','user_id','type_id','paid')) and
            record.get('user_status',record.get('status'))==2)
        for key in ('start_time','end_time'):
            actual=parse(record[key]); expected=parse(payload[key])
            valid=valid and actual.tzinfo is not None and actual==expected
        if not valid: raise ValueError()
    except (KeyError,TypeError,ValueError,AttributeError):
        raise WIWError('WIW did not confirm the approved time-off dates, employee, or status.') from None
    return record
