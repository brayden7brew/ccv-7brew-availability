from app.weekly import working_windows, week_totals
from app.models import Change
from test_portal import submit, sign_in


def test_working_period_excludes_nights_and_shows_split_availability():
    assert working_windows({'mode':'all_day'}) == [(300,1380)]
    assert working_windows({'mode':'none'}) == []
    assert working_windows({'mode':'unavailable','start':'10:00','end':'18:00'}) == [(300,600),(1080,1380)]
    assert working_windows({'mode':'hours','start':'01:00','end':'08:00'}) == [(300,480)]
    assert working_windows({'mode':'hours','start':'22:00','end':'00:00'}) == [(1320,1380)]
    assert week_totals({'days':[{'mode':'unavailable','start':'10:00','end':'18:00'}]*7}) == {'available':'70h','unavailable':'56h'}


def test_manager_review_has_working_hours_and_labeled_escaped_comment(client,db):
    submit(client)
    change=db.get(Change,1)
    change.employee_note='School schedule\nPlease keep Fridays free. <script>bad()</script>'
    db.commit()
    sign_in(client,'manager@test.local')
    html=client.get('/requests/1').text
    assert 'Available to work: 116 hours per week' in html  # six full 18-hour days and one 8-hour day
    assert 'Requested unavailable hours' in html
    assert 'Unavailable from 5:00 AM – 9:00 AM' in html
    assert 'Unavailable from 5:00 PM – 11:00 PM' in html
    assert '168-hour' not in html and 'including nights' not in html
    assert 'aria-labelledby="employee-comment-heading"' in html
    assert 'Employee’s comment to the manager' in html
    assert 'School schedule\nPlease keep Fridays free. &lt;script&gt;bad()&lt;/script&gt;' in html
    assert '<script>bad()</script>' not in html
