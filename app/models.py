from datetime import date, datetime, timezone
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base

def now():
    return datetime.now(timezone.utc)

class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    notification_email: Mapped[str] = mapped_column(String(254), default='', server_default='')
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default='true')
    notification_locations: Mapped[list | None] = mapped_column(JSON, nullable=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(Text)
    availability_access: Mapped[bool] = mapped_column(Boolean, default=True, server_default='true')
    ops_access: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')
    ops_locations: Mapped[list | None] = mapped_column(JSON, nullable=True)
    role: Mapped[str] = mapped_column(String(20), default='employee')
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    wiw_user_id: Mapped[int] = mapped_column(Integer, unique=True)
    location: Mapped[str] = mapped_column(String(120))
    secondary_location: Mapped[str] = mapped_column(String(120), default='', server_default='')

    extra_request_credits: Mapped[int] = mapped_column(Integer, default=0, server_default='0')

    minimum_hours_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default='true')
    minimum_available_minutes: Mapped[int] = mapped_column(Integer, default=900, server_default='900')
    notice_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default='true')
    notice_days: Mapped[int] = mapped_column(Integer, default=14, server_default='14')
    first_request_notice_exception: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')

class Scope(Base):
    __tablename__ = 'manager_scopes'
    __table_args__ = (UniqueConstraint('manager_id', 'location'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    manager_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    location: Mapped[str] = mapped_column(String(120))

class LoginSession(Base):
    __tablename__ = 'sessions'
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    expires: Mapped[datetime] = mapped_column(DateTime(timezone=True))

class LoginAttempt(Base):
    __tablename__ = 'login_attempts'
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), index=True)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)

class Change(Base):
    __tablename__ = 'changes'
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    location: Mapped[str] = mapped_column(String(120))
    secondary_location: Mapped[str] = mapped_column(String(120), default='', server_default='')
    wiw_user_id: Mapped[int] = mapped_column(Integer)
    request_limit_exempt: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')
    action: Mapped[str] = mapped_column(String(20))
    event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proposed: Mapped[dict] = mapped_column(JSON)
    before: Mapped[dict] = mapped_column(JSON)
    read_start: Mapped[str] = mapped_column(String(60))
    read_end: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(40), default='pending', index=True)
    employee_note: Mapped[str] = mapped_column(Text, default='')
    manager_note: Mapped[str] = mapped_column(Text, default='')
    manager_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    dry_run: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Audit(Base):
    __tablename__ = 'audit'
    id: Mapped[int] = mapped_column(primary_key=True)
    change_id: Mapped[int] = mapped_column(ForeignKey('changes.id'), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    event: Mapped[str] = mapped_column(String(60))
    details: Mapped[dict] = mapped_column(JSON)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class WeeklySchedule(Base):
    __tablename__ = 'weekly_schedules'
    id: Mapped[int] = mapped_column(primary_key=True)
    change_id: Mapped[int] = mapped_column(ForeignKey('changes.id'), unique=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    effective_date: Mapped[date] = mapped_column(Date)
    days: Mapped[list] = mapped_column(JSON)
    dry_run: Mapped[bool] = mapped_column(Boolean)

class ManagedEvent(Base):
    __tablename__ = 'managed_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    event_id: Mapped[int] = mapped_column(Integer, unique=True)
    snapshot: Mapped[dict] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

class AdminAudit(Base):
    __tablename__ = 'admin_audit'
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    target_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    details: Mapped[dict] = mapped_column(JSON)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Location(Base):
    __tablename__ = 'locations'
    name: Mapped[str] = mapped_column(String(120), primary_key=True)

class EmailOutbox(Base):
    __tablename__ = 'email_outbox'
    id: Mapped[int] = mapped_column(primary_key=True)
    change_id: Mapped[int] = mapped_column(ForeignKey('changes.id'))
    recipient_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    event: Mapped[str] = mapped_column(String(40))
    recipient: Mapped[str] = mapped_column(String(254))
    subject: Mapped[str] = mapped_column(String(254))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default='queued')
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(String(120), default='')
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    next_attempt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint('change_id','recipient_id','event'),)

class PortalSetup(Base):
    __tablename__ = 'portal_setup'
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), primary_key=True)
    digest: Mapped[str] = mapped_column(String(64), unique=True)
    email: Mapped[str] = mapped_column(String(254))
    expires: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    credential_version: Mapped[str] = mapped_column(String(64))

class WebhookBatch(Base):
    __tablename__ = 'webhook_batches'
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer)
    user_ids: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default='queued')
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    next_attempt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_error: Mapped[str] = mapped_column(String(120), default='')

class WIWCredential(Base):
    __tablename__ = 'wiw_credentials'
    id: Mapped[int] = mapped_column(primary_key=True)
    seed_hash: Mapped[str] = mapped_column(String(64), default='', server_default='')
    encrypted_token: Mapped[str] = mapped_column(Text, default='', server_default='')
    next_attempt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str] = mapped_column(String(300), default='', server_default='')
