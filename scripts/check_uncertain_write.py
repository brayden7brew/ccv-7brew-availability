"""Read-only check of the first uncertain test-site write. Never retries."""
import os
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
from app.config import settings
from app.db import SessionLocal
from app.models import Change
from app.wiw import WIW, WIWError

cfg = settings()
if cfg.wiw_account_id != 4319477 or cfg.wiw_context_user_id != 53517822:
    raise SystemExit("Stopped: configured workplace is not the test site.")
with SessionLocal() as db:
    change = db.get(Change, 3)
    if not change or change.wiw_user_id != 53634927:
        raise SystemExit("Stopped: expected test request was not found.")
    print("Read-only check of request #3 for Blake in 7 Brew- Test Site.")
    print("Saved request status:", change.status)
    try:
        result = WIW().read(change.wiw_user_id, change.read_start, change.read_end)
    except WIWError as exc:
        raise SystemExit(str(exc))
    events = result["availabilityevents"]
    print("WIW events found:", len(events))
    for event in events:
        print({key: event.get(key) for key in (
            "id", "type", "start_time", "end_time", "all_day", "recurrence")})
print("Done. No changes made. Share this output; credentials are omitted.")
