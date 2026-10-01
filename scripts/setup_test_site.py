"""Interactive local-only dry-run setup. Never prints or sends the WIW token to chat."""
import getpass
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
ENV = ROOT / '.env'

def main():
    live_requested = '--live' in sys.argv[1:]
    print('7 Brew- Test Site — local approval test')
    if not ENV.exists():
        token = getpass.getpass('Paste your WIW token (hidden), then press Return: ').strip()
        if not token or any(c.isspace() for c in token) or any(c in token for c in "\"'\\#"):
            raise SystemExit('Enter only the token, without quotes or the word Bearer. Nothing saved.')
        config = (
            f'SECRET_KEY={secrets.token_urlsafe(48)}\n'
            'DATABASE_URL=sqlite:///./portal-test.db\n'
            'ENVIRONMENT=development\nALLOWED_HOSTS=localhost,127.0.0.1\n'
            'SECURE_COOKIES=false\nDRY_RUN=true\nWIW_MODE=live\n'
            f'WIW_TOKEN={token}\nWIW_CONTEXT_USER_ID=53517822\nWIW_ACCOUNT_ID=4319477\n'
            'BUSINESS_TIMEZONE=America/New_York\nMINIMUM_NOTICE_DAYS=1\nSESSION_HOURS=8\n'
        )
        fd = os.open(ENV, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(config)
        print('Saved private local settings. Token will not be displayed.')
    from app.config import settings
    cfg = settings()
    if cfg.dry_run is False and not live_requested:
        raise SystemExit('Live test mode is configured. Start with: zsh start-test-site.command --live')
    if (cfg.wiw_mode != 'live' or cfg.wiw_account_id != 4319477
            or cfg.wiw_context_user_id != 53517822 or cfg.environment != 'development'
            or cfg.database_url != 'sqlite:///./portal-test.db'):
        raise SystemExit('Existing settings differ from this local test. No changes made to them.')
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], check=True)
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models import User, Scope
    from app.security import hasher
    with SessionLocal() as db:
        for email, name, role, wiw_id in [
            ('blake@portal.test', 'Blake Stotler', 'employee', 53634927),
            ('brayden@portal.test', 'Brayden Stotler', 'manager', 53517822),
        ]:
            user = db.scalar(select(User).where(User.wiw_user_id == wiw_id))
            if user:
                print(f'{user.email} already configured; password and access unchanged.')
                continue
            if db.scalar(select(User).where(User.email == email)):
                raise SystemExit('Setup email belongs to another WIW user. No account created.')
            print(f'Choose a NEW local portal password for {email} (not your WIW password).')
            while True:
                value = getpass.getpass('Password (8+ characters): ')
                confirmation = getpass.getpass('Confirm password: ')
                if len(value) >= 8 and value == confirmation:
                    break
                print('Passwords must match and contain at least 8 characters. Try again.')
            user = User(email=email, name=name, role=role, wiw_user_id=wiw_id,
                        location='7 Brew- Test Site', password_hash=hasher.hash(value))
            db.add(user)
            db.flush()
            # Bootstrap scope only once; never restore access an admin removed.
            if role == 'manager':
                db.add(Scope(manager_id=user.id, location='7 Brew- Test Site'))
        db.commit()
    print('\nSetup complete. ' + ('WIW writes are disabled.' if cfg.dry_run else 'LIVE TEST MODE: new manager approvals write to 7 Brew- Test Site.'))
    print('Starting the portal at http://127.0.0.1:8000 — stop with Control-C.')
    subprocess.run([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000', '--reload', '--reload-dir', 'app'], check=True)

if __name__ == '__main__':
    main()
