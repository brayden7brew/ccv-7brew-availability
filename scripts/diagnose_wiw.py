"""Read-only WIW diagnostics. No writes; no credentials or employee records printed."""
import json
import os
from pathlib import Path
import re
import sys
from datetime import datetime,timedelta
from email.utils import format_datetime
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0,str(ROOT))
from app.config import settings
import httpx

def safe_error(response,token):
    try: body=response.json()
    except ValueError: return 'Non-JSON error response; body omitted.'
    if not isinstance(body,dict): return 'Unexpected error response; body omitted.'
    safe={k:v for k,v in body.items() if k in ('error','errors','message','code','error_code')}
    def scrub(value):
        if isinstance(value,dict):
            return {k:scrub(v) for k,v in value.items() if not any(word in k.lower() for word in ('token','password','secret','authorization','email'))}
        if isinstance(value,list): return [scrub(v) for v in value[:5]]
        if isinstance(value,str):
            value=value.replace(token,'[REDACTED]')
            value=re.sub(r'eyJ[\w-]+\.[\w-]+\.[\w-]+','[REDACTED]',value)
            return value[:600]
        return value
    return json.dumps(scrub(safe))[:1500] if safe else 'No standard error fields. Body omitted.'

def main():
    cfg=settings()
    if cfg.wiw_account_id!=4319477 or cfg.wiw_context_user_id!=53517822:
        raise SystemExit('This diagnostic is limited to the configured test workplace.')
    if not cfg.wiw_token: raise SystemExit('No token configured in the local .env file.')
    start=datetime.now(ZoneInfo(cfg.business_timezone)).replace(hour=0,minute=0,second=0,microsecond=0)
    common={'user_id':53634927}
    trials=[('Default date range',common),
        ('14 days, ISO dates',{**common,'start':start.isoformat(),'end':(start+timedelta(days=14)).isoformat()}),
        ('14 days, documented example date format',{**common,'start':format_datetime(start),'end':format_datetime(start+timedelta(days=14))}),
        ('400 days, ISO dates',{**common,'start':start.isoformat(),'end':(start+timedelta(days=400)).isoformat()})]
    print('Read-only test for Blake in 7 Brew- Test Site. No availability changes.')
    with httpx.Client(timeout=20,follow_redirects=False,headers={
        'Authorization':f'Bearer {cfg.wiw_token}','W-UserID':str(cfg.wiw_context_user_id)}) as client:
        for label,params in trials:
            try: response=client.get('https://api.wheniwork.com/2/availabilityevents',params=params)
            except httpx.HTTPError as exc:
                print(label+': '+type(exc).__name__+' (connection failed)')
                break
            print(label+': HTTP '+str(response.status_code))
            if not response.is_success:
                print('  '+safe_error(response,cfg.wiw_token))
                if response.status_code in (401,403,429): break
    print('Done. You can share these diagnostic lines; token and employee records are omitted.')

if __name__=='__main__':main()
