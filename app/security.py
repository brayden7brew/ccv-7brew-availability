import hashlib
import secrets
from datetime import timedelta, timezone
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import HTTPException
from sqlalchemy import select
from .models import User, LoginSession, now
from .config import settings

hasher = PasswordHasher()
DUMMY = hasher.hash(secrets.token_urlsafe(32))

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def verify(password, encoded):
    try:
        return hasher.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False

def current_user(request, db):
    token = request.session.get('sid', '')
    session = db.get(LoginSession, digest(token)) if token else None
    if not session or session.expires.replace(tzinfo=timezone.utc) <= now():
        raise HTTPException(401, 'Please sign in again.')
    user = db.get(User, session.user_id)
    if not user or not user.active:
        raise HTTPException(401, 'Account disabled.')
    return user

def login(request, db, user, *, remember=False):
    old = request.session.get('sid')
    if old:
        item = db.get(LoginSession, digest(old))
        if item: db.delete(item)
    request.session.clear()
    token = secrets.token_urlsafe(32)
    request.session.update(sid=token, csrf=secrets.token_urlsafe(32), remember_device=remember)
    db.add(LoginSession(digest=digest(token), user_id=user.id,
                        expires=now() + (timedelta(days=60) if remember else timedelta(hours=settings().session_hours))))
    db.commit()

def csrf(request, value):
    if not value or not secrets.compare_digest(request.session.get('csrf', ''), value):
        raise HTTPException(403, 'Form expired. Reload the page and try again.')
