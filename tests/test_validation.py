from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import pytest
from pydantic import ValidationError
from app.validation import EventInput
from app.config import Settings

def future():
    return (datetime.now(ZoneInfo('America/New_York'))+timedelta(days=30)).replace(hour=9,minute=0,second=0,microsecond=0)

@pytest.mark.parametrize('change',[{'type':3},{'weeks':53},{'notes':'x'*161},{'all_day':True}])
def test_bad_event_values(change):
    start=future()
    with pytest.raises(ValidationError): EventInput(**({'start_time':start,'end_time':start+timedelta(hours=1),'type':1}|change))


def test_overnight_and_all_day():
    start=future().replace(hour=22)
    assert EventInput(start_time=start,end_time=start+timedelta(hours=8),type=1).payload()['type']==1
    start=start.replace(hour=0)
    assert EventInput(start_time=start,end_time=start+timedelta(days=1),type=2,all_day=True).all_day


def test_production_settings_fail_closed():
    with pytest.raises(ValueError): Settings(secret_key='x'*40,environment='production',database_url='sqlite://')
    with pytest.raises(ValueError): Settings(secret_key='x'*40,dry_run=False,wiw_mode='demo')
