"""WIW Authentication API 1.9.1; credentials/tokens remain request-local only."""
import httpx
from .config import settings

class WIWAuthError(Exception):
    pass

SIGN_IN_FAILED = ('WIW could not complete sign-in. Check your WIW email and password. '
    'If your account requires two-step verification or company SSO, use your portal login or contact the portal administrator.')

class WIWAuth:
    def __init__(self, transport=None):
        self.cfg = settings()
        self.transport = transport

    def authenticate(self, email, password):
        if not self.cfg.wiw_developer_key or not self.cfg.wiw_account_id:
            raise WIWAuthError('WIW sign-in is not configured. Use your portal login.')
        try:
            with httpx.Client(timeout=20, follow_redirects=False, transport=self.transport) as client:
                response = client.post('https://api.login.wheniwork.com/login',
                    headers={'W-Key':self.cfg.wiw_developer_key},
                    json={'email':email,'password':password})
                if response.status_code == 429:
                    raise WIWAuthError('WIW is limiting sign-in attempts. Wait a few minutes and try again.')
                if response.status_code != 200:
                    raise WIWAuthError(SIGN_IN_FAILED)
                data = response.json()
                if not isinstance(data, dict): raise WIWAuthError(SIGN_IN_FAILED)
                person = data.get('person')
                if not isinstance(person, dict): raise WIWAuthError(SIGN_IN_FAILED)
                # Both token locations are documented in the official 200 example.
                token = data.get('token') or person.get('token')
                person_id = person.get('id')
                if (not isinstance(token,str) or not token or any(c.isspace() for c in token)
                        or isinstance(person_id,bool) or not str(person_id).isdigit()):
                    raise WIWAuthError(SIGN_IN_FAILED)
                response = client.get('https://api.wheniwork.com/2/login',
                    headers={'Authorization':f'Bearer {token}'})
                if response.status_code != 200:
                    raise WIWAuthError(SIGN_IN_FAILED)
                memberships = response.json()
                users = memberships.get('users') if isinstance(memberships,dict) else None
                if not isinstance(users,list): raise WIWAuthError(SIGN_IN_FAILED)
                matches = [u for u in users if isinstance(u,dict)
                    and u.get('account_id') == self.cfg.wiw_account_id
                    and str(u.get('login_id')) == str(person_id)
                    and u.get('activated') is True and u.get('is_deleted') is False
                    and type(u.get('id')) is int]
                if len(matches) != 1:
                    raise WIWAuthError('Your WIW login does not have an active membership in this portal’s workplace. Contact the portal administrator.')
                member = matches[0]
                self.display_name = ' '.join(str(member.get(k) or '') for k in ('first_name', 'last_name')).strip()[:120]
                return member['id']
        except (httpx.HTTPError, ValueError, TypeError):
            # Do not propagate request objects or upstream bodies containing secrets.
            raise WIWAuthError('WIW sign-in is temporarily unavailable. Use your portal login or try again later.') from None
