"""Transactional notification outbox. No network calls in approval transactions."""
import re
from sqlalchemy import select
from .config import settings
from .models import User, Scope, EmailOutbox
from .locations import assigned_locations


def valid_email(value):
    return bool(value and len(value)<=254 and re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}",value)
        and not value.lower().endswith(('.invalid','.test','.local')))


def enqueue_notifications(db, change, event):
    cfg=settings()
    if not cfg.email_enabled:
        return
    employee=db.get(User,change.employee_id)
    if event=='submitted':
        recipients=list(db.scalars(select(User).join(Scope,Scope.manager_id==User.id).where(
            User.active.is_(True), User.role.in_(['manager','admin']), User.id!=employee.id,
            Scope.location.in_(assigned_locations(change))).distinct()))
        subject=f'{employee.name} requested an availability change'
        text=f'{employee.name} submitted new weekly availability starting {change.proposed.get("effective_date", "the requested date")}.\nLocations: {", ".join(assigned_locations(change))}.\nPlease sign in to review and approve or decline.'
    else:
        recipients=[employee]
        approved=event=='approved'
        subject='Your availability request was approved' if approved else 'Your availability request was declined'
        text=f'Hi {employee.name},\n\nYour weekly availability request has been {"approved" if approved else "declined"}.'
        if approved:
            text+='\nThis confirms the manager decision. Open the request to check the When I Work sync status.'
            if change.dry_run: text+='\nThis was a test approval; When I Work was not changed.'
        if change.manager_note: text+=f'\n\nManager note: {change.manager_note}'
    link=cfg.public_base_url.rstrip('/')+f'/requests/{change.id}'
    for user in recipients:
        if db.scalar(select(EmailOutbox.id).where(EmailOutbox.change_id==change.id,
                EmailOutbox.recipient_id==user.id,EmailOutbox.event==event)):
            continue
        address=user.notification_email or user.email
        valid=valid_email(address)
        db.add(EmailOutbox(change_id=change.id,recipient_id=user.id,event=event,
            recipient=address if valid else '',subject=' '.join(subject.split())[:254],
            body=text+f'\n\nView request: {link}\n\nCCV 7 Brew Availability',
            status='queued' if valid else 'missing_address'))
