from datetime import timedelta
from app.models import Change, Audit, now
from test_portal import sign_in


def test_attention_includes_older_requests_but_respects_scope_and_archival(client, db):
    def add(status, location='North', employee=1, action='weekly', age=100):
        c = Change(employee_id=employee, location=location, wiw_user_id=employee,
                   action=action, status=status, proposed={'effective_date':'2026-12-01'},
                   before={}, read_start='', read_end='', created=now()-timedelta(days=age))
        db.add(c)
        db.flush()
        return c.id
    wanted = [add(status) for status in ('pending','approval_failed','needs_reconciliation')]
    hidden = [add('pending', 'South', 3), add('approval_failed', 'South', 3),
              add('pending', employee=2), add('pending', action='time_off')]
    archived = add('needs_reconciliation')
    hidden.append(archived)
    db.add(Audit(change_id=archived, actor_id=2, event='employee_archived', details={}))
    for _ in range(50):
        add('applied', age=0)
    db.commit()
    sign_in(client, 'manager@test.local')
    html = client.get('/requests').text
    attention = html.split('<h2>Pending approvals and requests needing attention</h2>')[1].split('<h2>All requests</h2>')[0]
    for ident in wanted:
        assert f'href="/requests/{ident}"' in attention
    for ident in hidden:
        assert f'href="/requests/{ident}"' not in attention
    assert 'href="/requests?page_number=2"' in html
    second = client.get('/requests?page_number=2').text
    assert 'Pending approvals and requests needing attention' not in second
    assert 'href="/requests?page_number=1"' in second
    assert f'href="/requests/{wanted[0]}"' in second
