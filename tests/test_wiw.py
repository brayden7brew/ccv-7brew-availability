import json
from types import SimpleNamespace
import httpx
import pytest
from app.config import settings
from app.wiw import WIW, WIWError

def live(monkeypatch):
    for k,v in {'wiw_mode':'live','dry_run':False,'wiw_token':'server-only-secret', 'wiw_context_user_id':90,'wiw_account_id':10}.items():
        monkeypatch.setattr(settings(),k,v)

def test_official_read_and_write_contract(monkeypatch):
    live(monkeypatch)
    calls=[]
    def handle(request):
        calls.append(request)
        assert request.headers['W-UserID']=='90'
        assert request.headers['Authorization']=='Bearer server-only-secret'
        if request.method=='GET' and request.url.path.endswith('/1'):
            return httpx.Response(200,json={'availabilityevent':{'id':1,'account_id':10,'user_id':20}})
        if request.method=='GET':
            assert request.url.params['user_id']=='20'
            return httpx.Response(200,json={'availabilityevents':[{'id':1,'account_id':10,'user_id':20}]})
        body=json.loads(request.content)
        assert body == {'user_id':20,'account_id':10,'type':1,'start_time':'2030-01-01T10:00:00-05:00','end_time':'2030-01-01T11:00:00-05:00'}
        return httpx.Response(200,json={'availabilityevent':{'id':1}})
    provider=WIW(httpx.MockTransport(handle))
    provider.read(20,'2030-01-01','2030-02-01')
    change=SimpleNamespace(status='applying', manager_id=2,dry_run=False,action='create',wiw_user_id=20,
        proposed={'type':1,'start_time':'2030-01-01T10:00:00-05:00','end_time':'2030-01-01T11:00:00-05:00'})
    provider.apply(change)
    assert [r.method for r in calls]==['GET','GET','POST']
    assert [r.url.path for r in calls]==['/2/availabilityevents','/2/availabilityevents/1','/2/availabilityevents']

@pytest.mark.parametrize('status,dry', [('pending',False),('rejected',False),('applying',True),('approved_dry_run',True)])
def test_write_gate(monkeypatch,status,dry):
    live(monkeypatch)
    with pytest.raises(WIWError): WIW().apply(SimpleNamespace(status=status,manager_id=2,dry_run=dry))


def test_wrong_account_and_errors(monkeypatch):
    live(monkeypatch)
    for response in [httpx.Response(200,json={'availabilityevents':[{'id':1,'user_id':20,'account_id':99}]}),
                     httpx.Response(403,text='secret upstream text'), httpx.Response(200,text='not json')]:
        provider=WIW(httpx.MockTransport(lambda request: response))
        with pytest.raises(WIWError) as e: provider.read(20,'2030-01-01','2030-01-15')
        assert 'secret' not in str(e.value)


def test_update_and_delete_paths(monkeypatch):
    live(monkeypatch)
    calls=[]
    def handle(request):
        calls.append(request)
        return httpx.Response(200,json={'availabilityevents':[]} if request.method=='PUT' else {'success':True})
    provider=WIW(httpx.MockTransport(handle))
    c=SimpleNamespace(status='applying', manager_id=2,dry_run=False,action='update',wiw_user_id=20,proposed={'type':2},event_id=123)
    provider.apply(c); c.action='delete'; provider.apply(c)
    assert [(r.method,r.url.path) for r in calls]==[('PUT','/2/availabilityevents/123'),('DELETE','/2/availabilityevents/123')]


def test_weekly_operation_uses_documented_fields_and_write_gate(monkeypatch):
    live(monkeypatch)
    calls=[]
    def handle(request):
        calls.append(request)
        if request.method=='DELETE': return httpx.Response(200,json={'success':True})
        payload=json.loads(request.content)
        assert set(payload)=={'type','start_time','end_time','all_day','notes','recurrence','user_id','account_id'}
        assert payload['type']==1 and payload['recurrence']=='FREQ=WEEKLY'
        return httpx.Response(200,json={'availabilityevent':{'id':444,**payload}})
    provider=WIW(httpx.MockTransport(handle))
    c=SimpleNamespace(action='weekly',status='applying',manager_id=2,dry_run=False,wiw_user_id=20)
    op={'action':'create','payload':{'type':1,'start_time':'2030-01-06T00:00:00-05:00','end_time':'2030-01-06T09:00:00-05:00','all_day':False,'notes':'Portal','recurrence':'FREQ=WEEKLY'}}
    provider.weekly_operation(c,op)
    provider.weekly_operation(c,{'action':'delete','event_id':444})
    assert [(r.method,r.url.path) for r in calls]==[('POST','/2/availabilityevents'),('DELETE','/2/availabilityevents/444')]
    c.dry_run=True
    with pytest.raises(WIWError): provider.weekly_operation(c,op)
    assert len(calls)==2


def test_long_reads_use_90_day_windows_and_deduplicate(monkeypatch):
    from datetime import datetime, timedelta, timezone
    live(monkeypatch)
    calls=[]
    repeated={'id':1,'user_id':20,'account_id':10,'recurrence':'FREQ=WEEKLY'}
    def handle(request):
        if request.url.path != '/2/availabilityevents':
            event_id=int(request.url.path.rsplit('/',1)[1])
            return httpx.Response(200,json={'availabilityevent':{'id':event_id,'user_id':20,'account_id':10}})
        start=datetime.fromisoformat(request.url.params['start'])
        end=datetime.fromisoformat(request.url.params['end'])
        assert timedelta(0)<end-start<=timedelta(days=90)
        calls.append((start,end))
        return httpx.Response(200,json={'availabilityevents':[repeated,{'id':len(calls)+1,'user_id':20,'account_id':10}]})
    first=datetime(2030,1,1,tzinfo=timezone.utc)
    last=first+timedelta(days=400)
    result=WIW(httpx.MockTransport(handle)).read(20,first.isoformat(),last.isoformat())
    assert len(calls)==5
    assert calls[0][0]==first and calls[-1][1]==last
    assert all(calls[i][1]==calls[i+1][0] for i in range(len(calls)-1))
    assert [e['id'] for e in result['availabilityevents']]==[1,2,3,4,5,6]

@pytest.mark.parametrize('failure',['http','wrong_user'])
def test_later_window_failure_never_returns_partial_state(monkeypatch,failure):
    live(monkeypatch)
    calls=[]
    def handle(request):
        calls.append(request)
        event={'id':1,'user_id':20,'account_id':10}
        if len(calls)>1:
            if failure=='http': return httpx.Response(403,json={'error':'Denied'})
            if failure=='wrong_user': event['user_id']=99
        return httpx.Response(200,json={'availabilityevents':[event]})
    with pytest.raises(WIWError):
        WIW(httpx.MockTransport(handle)).read(20,'2030-01-01','2031-01-01')
    assert len(calls)==2

@pytest.mark.parametrize('start,end',[('bad','bad'),('2030-01-01','2030-01-01'),('2030-02-01','2030-01-01')])
def test_invalid_read_ranges_do_not_call_wiw(monkeypatch,start,end):
    live(monkeypatch)
    def handle(request): pytest.fail('Invalid range must not call WIW')
    with pytest.raises(WIWError): WIW(httpx.MockTransport(handle)).read(20,start,end)


def test_error_preserves_numeric_code_without_upstream_secrets(monkeypatch):
    live(monkeypatch)
    provider = WIW(httpx.MockTransport(lambda request: httpx.Response(400, json={
        'code': 2002, 'error': 'server-only-secret', 'token': 'private'})))
    with pytest.raises(WIWError) as caught:
        provider.call('POST', '/2/availabilityevents', payload={})
    assert 'HTTP 400' in str(caught.value)
    assert '2002' in str(caught.value)
    assert 'secret' not in str(caught.value)
    assert 'private' not in str(caught.value)


def test_error_explanation_redacts_credentials_and_email(monkeypatch):
    live(monkeypatch)
    monkeypatch.setattr(settings(), 'wiw_developer_key', 'developer-private')
    provider = WIW(httpx.MockTransport(lambda request: httpx.Response(400, json={
        'code': 2006, 'error': 'Invalid recurrence server-only-secret developer-private person@example.com',
        'password': 'never-show-this'})))
    with pytest.raises(WIWError) as caught:
        provider.call('POST', '/2/availabilityevents', payload={})
    message = str(caught.value)
    assert 'Invalid recurrence' in message
    for secret in ('server-only-secret', 'developer-private', 'person@example.com', 'never-show-this'):
        assert secret not in message


def test_error_metadata_distinguishes_auth_timeout_and_invalid_response(monkeypatch):
    live(monkeypatch)
    cases=[(httpx.Response(401,json={'code':1000,'error':'denied'}),'http_error',401,1000),
           (httpx.Response(200,text='invalid'),'invalid_response',None,None)]
    for response,reason,status,code in cases:
        with pytest.raises(WIWError) as caught:
            WIW(httpx.MockTransport(lambda request:response)).read(20,'2030-01-01','2030-01-15')
        assert (caught.value.reason,caught.value.http_status,caught.value.wiw_code)==(reason,status,code)
    def timeout(request): raise httpx.ReadTimeout('private request details',request=request)
    with pytest.raises(WIWError) as caught:
        WIW(httpx.MockTransport(timeout)).read(20,'2030-01-01','2030-01-15')
    assert caught.value.reason=='timeout'


def test_recurring_occurrences_use_saved_event_and_detect_later_edits(monkeypatch):
    from app.workflow import canonical
    live(monkeypatch)
    saved={'id':1,'user_id':20,'account_id':10,'recurrence':'FREQ=WEEKLY;BYDAY=SU',
           'start_time':'2030-01-06T05:00:00-05:00','end_time':'2030-01-06T10:00:00-05:00'}
    detail_calls=[]
    def handle(request):
        if request.url.path=='/2/availabilityevents/1':
            detail_calls.append(request)
            return httpx.Response(200,json={'availabilityevent':saved})
        # Occurrences differ both within one window and across windows.
        occurrence={**saved,'start_time':request.url.params['start'],
                    'end_time':request.url.params['end']}
        return httpx.Response(200,json={'availabilityevents':[saved,occurrence]})
    provider=WIW(httpx.MockTransport(handle))
    before=provider.read(20,'2030-01-01','2031-01-01')
    assert before=={'availabilityevents':[saved]}
    assert len(detail_calls)==1
    assert canonical(provider.read(20,'2030-01-01','2031-01-01'))==canonical(before)
    saved['notes']='Real edit after submission'
    assert canonical(provider.read(20,'2030-01-01','2031-01-01'))!=canonical(before)


@pytest.mark.parametrize('detail', [None, [], {},
    {'id':2,'user_id':20,'account_id':10},
    {'id':1,'user_id':99,'account_id':10},
    {'id':1,'user_id':20,'account_id':99}])
def test_saved_event_identity_is_verified(monkeypatch, detail):
    live(monkeypatch)
    def handle(request):
        if request.url.path=='/2/availabilityevents':
            return httpx.Response(200,json={'availabilityevents':[{'id':1,'user_id':20,'account_id':10}]})
        return httpx.Response(200,json={'availabilityevent':detail})
    with pytest.raises(WIWError) as caught:
        WIW(httpx.MockTransport(handle)).read(20,'2030-01-01','2030-02-01')
    assert caught.value.reason=='event_identity_mismatch'


def test_deleted_saved_event_never_returns_partial_state(monkeypatch):
    live(monkeypatch)
    def handle(request):
        if request.url.path=='/2/availabilityevents':
            return httpx.Response(200,json={'availabilityevents':[{'id':1,'user_id':20,'account_id':10}]})
        return httpx.Response(404,json={'error':'Not found'})
    with pytest.raises(WIWError):
        WIW(httpx.MockTransport(handle)).read(20,'2030-01-01','2030-02-01')


@pytest.mark.parametrize('unchanged',[False,True])
def test_weekly_update_verifies_saved_recurrence(monkeypatch,unchanged):
    live(monkeypatch)
    payload={'type':1,'start_time':'2030-01-01T05:00:00-05:00',
             'end_time':'2030-01-01T10:00:00-05:00','recurrence':'FREQ=DAILY;COUNT=5'}
    event={'id':77,'user_id':20,'account_id':10,**payload}
    def handle(request):
        assert request.url.path=='/2/availabilityevents/77'
        if request.method=='PUT':
            assert json.loads(request.content)=={**payload,'user_id':20,'account_id':10}
            return httpx.Response(200,json={'availabilityevents':[event]})
        return httpx.Response(200,json={'availabilityevent':{**event,'recurrence':'FREQ=DAILY'} if unchanged else event})
    change=SimpleNamespace(action='weekly',status='applying',manager_id=2,dry_run=False,wiw_user_id=20)
    provider=WIW(httpx.MockTransport(handle))
    operation={'action':'update','event_id':77,'payload':payload}
    if unchanged:
        with pytest.raises(WIWError): provider.weekly_operation(change,operation)
    else: assert provider.weekly_operation(change,operation)['availabilityevents']==[event]
