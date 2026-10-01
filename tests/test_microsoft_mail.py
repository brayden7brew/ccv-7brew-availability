import json
from types import SimpleNamespace
import httpx
import pytest
from app.config import settings
from app.microsoft_mail import send_graph, MicrosoftMailError

@pytest.mark.parametrize('status',[202,403])
def test_graph_shared_mailbox_contract(monkeypatch,status):
    cfg=settings()
    for key,value in {'microsoft_tenant_id':'11111111-1111-1111-1111-111111111111',
        'microsoft_client_id':'22222222-2222-2222-2222-222222222222','microsoft_client_secret':'private-secret',
        'email_from':'alerts@rva7brew.com','public_base_url':'https://portal.example.com'}.items():
        monkeypatch.setattr(cfg,key,value)
    calls=[]
    def handle(request):
        calls.append(request)
        if len(calls)==1:
            assert request.url.host=='login.microsoftonline.com'
            assert b'grant_type=client_credentials' in request.content
            return httpx.Response(200,json={'access_token':'private-token'})
        assert request.url.host=='graph.microsoft.com'
        assert request.url.path=='/v1.0/users/alerts@rva7brew.com/sendMail'
        data=json.loads(request.content)
        assert data['message']['toRecipients']==[{'emailAddress':{'address':'employee@example.com'}}]
        assert '&lt;script&gt;' in data['message']['body']['content']
        assert 'https://portal.example.com/requests/42' in data['message']['body']['content']
        return httpx.Response(status,json={'error':'private-secret private-token'} if status!=202 else None)
    item=SimpleNamespace(subject='Approved',body='<script>',recipient='employee@example.com',change_id=42)
    if status==202: send_graph(item,httpx.MockTransport(handle))
    else:
        with pytest.raises(MicrosoftMailError) as exc:send_graph(item,httpx.MockTransport(handle))
        assert 'private' not in str(exc.value)
    assert len(calls)==2
