"""Run `python -m app.mailer` in a worker; --once sends one batch."""
import argparse
import html
import smtplib
import ssl
import time
from datetime import timedelta
from email.message import EmailMessage
from sqlalchemy import select
from .config import settings
from .db import SessionLocal
from .models import EmailOutbox, User, Scope, Change, now
from .locations import assigned_locations
from .notifications import valid_email, wants_notifications


def send_email(item):
    cfg=settings()
    if cfg.email_provider=='microsoft_graph':
        from .microsoft_mail import send_graph
        return send_graph(item)
    message=EmailMessage()
    message['From']=cfg.email_from
    message['To']=item.recipient
    message['Subject']=item.subject
    message['Message-ID']=f'<ccv-{item.id}@{cfg.email_from.split("@")[-1]}>'
    message.set_content(item.body)
    link=getattr(item, 'link', '') or cfg.public_base_url.rstrip('/')+f'/requests/{item.change_id}'
    label=getattr(item, 'link_label', 'View availability request')
    message.add_alternative('<html><body><p>'+html.escape(item.body).replace('\n','<br>')+
        '</p><p><a href="'+html.escape(link,quote=True)+'">'+html.escape(label)+'</a></p></body></html>',subtype='html')
    connection=smtplib.SMTP_SSL(cfg.smtp_host,cfg.smtp_port,timeout=20,context=ssl.create_default_context()) if cfg.smtp_ssl else smtplib.SMTP(cfg.smtp_host,cfg.smtp_port,timeout=20)
    with connection as server:
        if not cfg.smtp_ssl: server.starttls(context=ssl.create_default_context())
        if cfg.smtp_username: server.login(cfg.smtp_username,cfg.smtp_password)
        if server.send_message(message): raise RuntimeError('Recipient refused')


def process_batch(factory=SessionLocal,sender=send_email):
    if not settings().email_enabled: return 0
    sent=0
    for _ in range(20):
        with factory() as db:
            item=db.scalar(select(EmailOutbox).where(EmailOutbox.status.in_(['queued','retry']),
                EmailOutbox.next_attempt<=now()).order_by(EmailOutbox.id).limit(1).with_for_update(skip_locked=True))
            if not item: break
            user=db.get(User,item.recipient_id)
            change=db.get(Change,item.change_id)
            # Recheck current permission so revoked managers never get queued notifications.
            permitted=bool(user and user.active and change and wants_notifications(user, change))
            if permitted and item.event=='submitted':
                permitted=user.role in ('manager','admin') and user.id!=change.employee_id and bool(db.scalar(select(Scope.id).where(
                    Scope.manager_id==user.id,Scope.location == change.location))) and change.status=='pending'
            if not permitted:
                item.status='cancelled'; db.commit(); continue
            address=user.notification_email or user.email
            if not valid_email(address):
                item.status='missing_address'; db.commit(); continue
            item.recipient=address
            item.attempts+=1
            try:
                sender(item)
            except Exception as exc:
                # Never persist provider bodies, credentials, or connection objects.
                item.last_error=type(exc).__name__[:120]
                item.status='failed' if item.attempts>=5 else 'retry'
                item.next_attempt=now()+timedelta(minutes=min(60,2**item.attempts))
            else:
                item.status='sent'; item.last_error=''; sent+=1
            db.commit()
    return sent


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    if not settings().email_enabled:
        print('Email sending is disabled; worker will remain idle until configured and restarted.', flush=True)
        if args.once: return
    while True:
        from .webhooks import process_webhooks
        process_webhooks()
        process_batch()
        if args.once: break
        time.sleep(30)

if __name__=='__main__': main()
