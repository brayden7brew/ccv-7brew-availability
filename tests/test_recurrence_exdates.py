from datetime import date, datetime
from zoneinfo import ZoneInfo
import pytest
from app.legacy_availability import handover
from app.event_identity import finite_occurrences, verify_finite_replacement
from app.availability_preview import preview
from app.recurrence import parse_recurrence

ZONE = ZoneInfo('America/New_York')

def saved_event():
    return {'id':190615018,'type':2,'start_time':'Sat, 10 Oct 2026 04:00:00 +0000',
        'end_time':'Sun, 11 Oct 2026 03:59:59 +0000','all_day':True,
        'recurrence':'FREQ=WEEKLY;UNTIL=20261220T045959Z;BYDAY=SA;EXDATE=20261031T040000Z'}


def test_exact_request_173_event_preserves_skipped_day_and_dst():
    event=saved_event()
    cutoff=datetime(2026,11,18,tzinfo=ZONE)
    operations,retained=handover([event],cutoff.date())
    assert len(operations)==1 and operations[0]['action']=='update'
    payload=operations[0]['payload']
    assert 'EXDATE=20261031T040000Z' in payload['recurrence']
    assert 'UNTIL=20261118T045959Z' in payload['recurrence']
    expected=finite_occurrences([event],before=cutoff)
    assert finite_occurrences(retained)==expected
    dates=[datetime.fromisoformat(item[1]).astimezone(ZONE).date() for item in expected]
    assert dates==[date(2026,10,10),date(2026,10,17),date(2026,10,24),date(2026,11,7),date(2026,11,14)]
    verify_finite_replacement(retained,payload)
    restored={**payload,'recurrence':payload['recurrence'].replace(';EXDATE=20261031T040000Z','')}
    with pytest.raises(ValueError): verify_finite_replacement([restored],payload)


def test_preview_respects_exclusion():
    event={**saved_event(),'type':1}
    assert preview([event],date(2026,10,31))[0]['hours']=='5:00 AM–11:00 PM'
    assert preview([event],date(2026,11,7))[0]['hours']=='No available hours'


def test_multiple_exclusions_and_count():
    recurrence=parse_recurrence('RRULE:FREQ=WEEKLY;COUNT=4;BYDAY=SA;EXDATE=20261017T040000Z,20261024T040000Z',dtstart=datetime(2026,10,10,tzinfo=ZONE))
    assert [item.date() for item in recurrence]==[date(2026,10,10),date(2026,10,31)]


@pytest.mark.parametrize('value',['','20261031','20261031T040000','bad','20261331T040000Z'])
def test_invalid_exclusion_still_blocks_writes(value):
    event=saved_event();event['recurrence']='FREQ=WEEKLY;BYDAY=SA;EXDATE='+value
    with pytest.raises(ValueError): handover([event],date(2026,11,18))
