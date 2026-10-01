"""Per-device cookie lifetime; database session expiry remains authoritative."""
from starlette.middleware.sessions import SessionMiddleware

REMEMBER_SECONDS = 60 * 24 * 60 * 60


class DeviceSessionMiddleware(SessionMiddleware):
    def __init__(self, app, *, normal_max_age, **kwargs):
        self.normal_max_age = normal_max_age
        super().__init__(app, max_age=REMEMBER_SECONDS, **kwargs)

    async def __call__(self, scope, receive, send):
        async def send_with_lifetime(message):
            if message['type'] == 'http.response.start':
                lifetime = REMEMBER_SECONDS if scope.get('session', {}).get('remember_device') else self.normal_max_age
                headers = []
                for key, value in message.get('headers', []):
                    if key.lower() == b'set-cookie' and value.startswith((self.session_cookie + '=').encode()):
                        value = value.replace(f'Max-Age={REMEMBER_SECONDS}'.encode(), f'Max-Age={lifetime}'.encode())
                    headers.append((key, value))
                message['headers'] = headers
            await send(message)
        await super().__call__(scope, receive, send_with_lifetime)
