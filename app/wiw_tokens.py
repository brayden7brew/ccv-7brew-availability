"""Encrypted shared service token; JWT claims are scheduling hints, not authentication."""
import asyncio
import base64
import hashlib
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from .config import settings
from .db import SessionLocal
from .models import WIWCredential, now

logger = logging.getLogger(__name__)


def cipher():
    key = hashlib.sha256(b'ccv-wiw-token-v1\0' + settings().secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def refresh_due(token, instant):
    try:
        claims = json.loads(base64.urlsafe_b64decode(token.split('.')[1] + '==='))
        issued = claims['iat']
        if type(issued) not in (int, float): return instant
        issued = datetime.fromtimestamp(issued, timezone.utc)
        if issued > instant + timedelta(minutes=5): return instant
        due = issued + timedelta(days=4)
        expiry = claims.get('exp')
        if type(expiry) in (int, float):
            due = min(due, datetime.fromtimestamp(expiry, timezone.utc) - timedelta(days=2))
        return due
    except (ValueError, KeyError, IndexError, TypeError, OverflowError):
        return instant


def service_token(*, factory=None, transport=None):
    cfg = settings()
    if cfg.wiw_mode != 'live' or not cfg.wiw_auto_refresh:
        return cfg.wiw_token
    factory = factory or SessionLocal
    with factory() as db:
        # Singleton is inserted by migration, so first-run instances also share a lock.
        row = db.scalar(select(WIWCredential).where(WIWCredential.id == 1).with_for_update())
        if row is None:
            raise RuntimeError('WIW connection storage is not initialized. Run database migrations.')
        instant = now()
        seed = hashlib.sha256(f'{cfg.wiw_account_id}:{cfg.wiw_context_user_id}:'.encode() + cfg.wiw_token.encode()).hexdigest()
        if row.seed_hash != seed:
            row.seed_hash = seed
            row.encrypted_token = cipher().encrypt(cfg.wiw_token.encode()).decode()
            row.next_attempt = refresh_due(cfg.wiw_token, instant)
            row.last_success = None
            row.error = ''
        try:
            token = cipher().decrypt(row.encrypted_token.encode()).decode()
        except InvalidToken:
            row.error = 'Saved connection cannot be decrypted. Restore the portal secret key or supply a new WIW token.'
            db.commit()
            raise RuntimeError('Saved WIW connection requires administrator attention.') from None
        if utc(row.next_attempt) > instant:
            db.commit()
            return token
        # Failures back off, preserve the saved token, and never replay availability writes.
        row.next_attempt = instant + timedelta(hours=1)
        try:
            with httpx.Client(timeout=20, follow_redirects=False, transport=transport) as client:
                response = client.post('https://api.login.wheniwork.com/refresh',
                    headers={'Authorization': f'Bearer {token}'})
            if response.status_code != 200:
                row.error = ('When I Work sign-in must be renewed by an administrator.'
                             if response.status_code in (401,403) else
                             'When I Work token renewal failed. The portal will try again automatically.')
            else:
                data = response.json()
                replacement = data.get('token') if isinstance(data, dict) else None
                if not isinstance(replacement, str) or not replacement or not replacement.isascii() or len(replacement) > 32768 or any(c.isspace() for c in replacement):
                    raise ValueError()
                row.encrypted_token = cipher().encrypt(replacement.encode()).decode()
                row.last_success = instant
                row.error = ''
                # Without usable claims, check again in an hour rather than on every API call.
                row.next_attempt = max(instant + timedelta(hours=1), refresh_due(replacement, instant))
                token = replacement
        except (httpx.HTTPError, ValueError, UnicodeError):
            row.error = 'When I Work token renewal could not be confirmed. The portal will try again automatically.'
        db.commit()
        if row.error:
            logger.warning('WIW token renewal needs attention; see the administrator connection status.')
        return token


def connection_status(db):
    cfg = settings()
    if cfg.wiw_mode != 'live' or not cfg.wiw_auto_refresh:
        return dict(enabled=False, error='', last_success=None)
    row = db.get(WIWCredential, 1)
    if not row or not row.encrypted_token:
        return dict(enabled=True, error='Connection renewal is initializing.', last_success=None)
    overdue = utc(row.next_attempt) < now() - timedelta(hours=2)
    return dict(enabled=True, error=row.error or ('Automatic renewal is overdue. Check the portal service.' if overdue else ''), last_success=row.last_success)


@asynccontextmanager
async def token_lifespan(app):
    async def maintain():
        while True:
            try:
                await run_in_threadpool(service_token)
            except Exception:
                # Never log credential-bearing exception bodies or upstream responses.
                logger.error('WIW automatic renewal check failed. Check database and connection configuration.')
            await asyncio.sleep(3600)
    cfg = settings()
    task = asyncio.create_task(maintain()) if cfg.wiw_mode == 'live' and cfg.wiw_auto_refresh else None
    try:
        yield
    finally:
        if task:
            task.cancel()
            try: await task
            except asyncio.CancelledError: pass
