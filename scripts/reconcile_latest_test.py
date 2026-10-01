"""Reconcile the latest unresolved Blake test request using fresh WIW reads only."""
import os
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
from sqlalchemy import select
from app.config import settings
from app.db import SessionLocal
from app.models import Change, User
from app.wiw import WIW
from app.workflow import allowed
from app.weekly_workflow import reconcile_weekly
def main():
    cfg = settings()
    if cfg.wiw_account_id != 4319477 or cfg.wiw_context_user_id != 53517822 or cfg.wiw_mode != 'live':
        raise SystemExit('Stopped: live test workplace configuration required.')
    with SessionLocal() as db:
        changes = list(db.scalars(select(Change).where(Change.wiw_user_id == 53634927,
            Change.status.in_(['needs_reconciliation', 'applying']))))
        if len(changes) != 1:
            raise SystemExit('Expected exactly one unresolved test request; no changes made.')
        change = changes[0]
        actor = db.get(User, change.manager_id)
        if change.action != 'weekly' or not actor or not actor.active or actor.role not in ('manager','admin') or not allowed(db, actor, change):
            raise SystemExit('Authorized weekly approval required; no changes made.')
        reconcile_weekly(db, actor, change, 'not-applied',
            'Fresh WIW read exactly matches saved pre-write state. No changes applied.', WIW())
        db.commit()
        print(f'Request #{change.id} reconciled as not applied. No WIW writes performed.')

if __name__ == "__main__":
    main()
