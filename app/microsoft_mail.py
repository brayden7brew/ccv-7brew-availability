"""Microsoft Graph application-authenticated shared mailbox sending."""
from urllib.parse import quote
from uuid import UUID
import html
import httpx
from .config import settings

class MicrosoftMailError(Exception):
    pass

def send_graph(item, transport=None):
    cfg=settings()
    tenant=str(UUID(cfg.microsoft_tenant_id))
    link=cfg.public_base_url.rstrip('/')+f'/requests/{item.change_id}'
    body='<p>'+html.escape(item.body).replace('\n','<br>')+'</p><p><a href="'+html.escape(link,quote=True)+'">View request</a></p>'
    try:
        with httpx.Client(timeout=20,follow_redirects=False,transport=transport) as client:
            auth=client.post(f'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token',data={
                'client_id':cfg.microsoft_client_id,'client_secret':cfg.microsoft_client_secret,
                'scope':'https://graph.microsoft.com/.default','grant_type':'client_credentials'})
            if auth.status_code!=200:
                raise MicrosoftMailError(f'Microsoft authentication HTTP {auth.status_code}')
            data=auth.json()
            token=data.get('access_token') if isinstance(data,dict) else None
            if not isinstance(token,str) or not token or any(c.isspace() for c in token):
                raise MicrosoftMailError('Microsoft authentication response invalid')
            response=client.post('https://graph.microsoft.com/v1.0/users/'+quote(cfg.email_from,safe='')+'/sendMail',
                headers={'Authorization':f'Bearer {token}'},json={'message':{
                    'subject':item.subject,'body':{'contentType':'HTML','content':body},
                    'toRecipients':[{'emailAddress':{'address':item.recipient}}]}})
            if response.status_code!=202:
                raise MicrosoftMailError(f'Microsoft sendMail HTTP {response.status_code}')
    except (httpx.HTTPError,ValueError):
        raise MicrosoftMailError('Microsoft email service unavailable or invalid response') from None
