import re
from sqlalchemy import select
from app.config import settings
from app.models import User, AdminAudit
from test_portal import sign_in


def test_setup_email_link_and_code_not_audited(client,db,monkeypatch):
    cfg=settings()
    monkeypatch.setattr(cfg,'email_enabled',True)
    monkeypatch.setattr(cfg,'public_base_url','https://a.rva7brew.com')
    sent=[]
    monkeypatch.setattr('app.mailer.send_email',sent.append)
    db.get(User,2).role='admin';db.commit()
    csrf=sign_in(client,'manager@test.local')
    response=client.post('/admin/users/1/portal-login',data={'csrf':csrf,'email':'e@example.com','send_email':'on'})
    assert response.status_code==200 and len(sent)==1
    code=re.findall(r'<input readonly value="([^"]+)"',response.text)[1]
    assert code in sent[0].body
    assert sent[0].recipient=='e@example.com'
    assert sent[0].link=='https://a.rva7brew.com/setup'
    assert code not in str([a.details for a in db.scalars(select(AdminAudit))])
    assert 'accepted for delivery' in response.text


def test_email_failure_retains_manual_setup(client,db,monkeypatch):
    monkeypatch.setattr(settings(),'email_enabled',True)
    def fail(item): raise RuntimeError('secret provider response')
    monkeypatch.setattr('app.mailer.send_email',fail)
    db.get(User,2).role='admin';db.commit()
    csrf=sign_in(client,'manager@test.local')
    response=client.post('/admin/users/1/portal-login',data={'csrf':csrf,'email':'e@example.com','send_email':'on'})
    assert response.status_code==200
    assert 'could not be confirmed' in response.text
    assert 'secret provider response' not in response.text


def test_test_email_admin_only_and_disabled(client,db,monkeypatch):
    csrf=sign_in(client)
    assert client.post('/admin/email-test',data={'csrf':csrf}).status_code==403
    db.get(User,2).role='admin';db.get(User,2).notification_email='admin@example.com';db.commit()
    csrf=sign_in(client,'manager@test.local')
    assert client.post('/admin/email-test',data={'csrf':csrf}).status_code==422
    monkeypatch.setattr(settings(),'email_enabled',True)
    sent=[];monkeypatch.setattr('app.mailer.send_email',sent.append)
    assert client.post('/admin/email-test',data={}).status_code==403
    assert client.post('/admin/email-test',data={'csrf':csrf}).status_code==200
    assert sent[0].recipient=='admin@example.com'
