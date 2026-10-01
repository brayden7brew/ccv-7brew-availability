"""Operator-only commands. Run from a trusted terminal; passwords never enter arguments."""
import argparse
import getpass
from datetime import timedelta
from sqlalchemy import select, delete
from .db import SessionLocal
from .models import User, Scope, LoginSession, LoginAttempt, Change, now
from .security import hasher
from .workflow import audit
from .wiw import WIW

def event_summary(event):
    """Whitelist availability fields; never print notes or authentication data."""
    import json
    result = {key:event.get(key) for key in ('id','type','start_time','end_time','all_day','recurrence','created_at','updated_at')}
    children = event.get('events')
    result['events_shape'] = type(children).__name__
    if isinstance(children, list):
        result['events_count'] = len(children)
        result['child_ids'] = [item.get('id') for item in children[:20] if isinstance(item, dict)]
    return json.dumps(result, sort_keys=True)


def password():
    value = getpass.getpass('New portal password (at least 8 characters): ')
    if len(value) < 8 or value != getpass.getpass('Confirm password: '):
        raise SystemExit('Passwords must match and be at least 8 characters.')
    return hasher.hash(value)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('create-user')
    create.add_argument('--email', required=True)
    create.add_argument('--name', required=True)
    create.add_argument('--role', choices=['employee','manager','admin'], default='employee')
    create.add_argument('--wiw-user-id', type=int, required=True)
    create.add_argument('--location', required=True)
    for command in ['reset-password', 'disable-user']:
        sub = commands.add_parser(command)
        sub.add_argument('--email', required=True)
    scope = commands.add_parser('scope')
    scope.add_argument('--email', required=True)
    scope.add_argument('--location', required=True)
    scope.add_argument('--remove', action='store_true')
    commands.add_parser('cleanup')
    commands.add_parser('unresolved')
    inspect = commands.add_parser('inspect-request')
    inspect.add_argument('--id', type=int, required=True)
    resume = commands.add_parser('resume-verified-deletions')
    resume.add_argument('--id', type=int, required=True)
    resume.add_argument('--manager-email', required=True)
    reconcile = commands.add_parser('reconcile')
    reconcile.add_argument('--id', type=int, required=True)
    reconcile.add_argument('--manager-email', required=True)
    reconcile.add_argument('--outcome', choices=['applied','not-applied'], required=True)
    reconcile.add_argument('--note', required=True)
    args = parser.parse_args()
    with SessionLocal() as db:
        if args.command == 'create-user':
            from .locations import lock_admin_changes, check_admin_cap
            lock_admin_changes(db)
            if args.role == 'admin': check_admin_cap(db)
            if args.wiw_user_id <= 0: raise SystemExit('WIW user ID must be positive.')
            db.add(User(email=args.email.lower().strip(), name=args.name, role=args.role,
                wiw_user_id=args.wiw_user_id, location=args.location, password_hash=password()))
        elif args.command in ['reset-password','disable-user','scope']:
            user = db.scalar(select(User).where(User.email == args.email.lower().strip()))
            if not user: raise SystemExit('No such user.')
            if args.command == 'reset-password': user.password_hash = password()
            if args.command == 'disable-user': user.active = False
            if args.command in ['reset-password','disable-user']:
                db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
            else:
                if user.role not in ('manager','admin'): raise SystemExit('User must be a manager.')
                existing = db.scalar(select(Scope).where(Scope.manager_id == user.id, Scope.location == args.location))
                if args.remove and existing: db.delete(existing)
                elif not args.remove and not existing: db.add(Scope(manager_id=user.id, location=args.location))
        elif args.command == 'cleanup':
            db.execute(delete(LoginSession).where(LoginSession.expires < now()))
            db.execute(delete(LoginAttempt).where(LoginAttempt.created < now()-timedelta(days=1)))
        elif args.command == 'unresolved':
            for c in db.scalars(select(Change).where(Change.status.in_(['applying','needs_reconciliation']))):
                print(f'#{c.id} employee={c.employee_id} status={c.status}')
        elif args.command == 'inspect-request':
            from .models import Audit
            from .wiw import WIWError
            from .workflow import canonical
            change = db.get(Change, args.id)
            if not change: raise SystemExit('Request not found.')
            provider = WIW()
            approval = db.scalars(select(Audit).where(Audit.change_id == change.id,
                Audit.event == 'approved').order_by(Audit.id.desc())).first()
            entries = db.scalars(select(Audit).where(Audit.change_id == change.id,
                Audit.event.in_(['operation_started','operation_succeeded','write_uncertain'])).order_by(Audit.id)).all()
            print(f'Request #{change.id}: {change.status}')
            try:
                current = provider.read(change.wiw_user_id, change.read_start, change.read_end)
            except WIWError as exc:
                raise SystemExit(f'Read failed: reason={exc.reason} http_status={exc.http_status} wiw_code={exc.wiw_code}')
            before = approval.details['pre_write_state'] if approval else change.before
            print(f"Matches pre-write state: {canonical(current) == canonical(before)}")
            print('Current WIW event IDs: ' + ', '.join(str(e['id']) for e in current['availabilityevents']))
            for entry in entries:
                index = entry.details.get('index')
                print(f'Operation {index}: {entry.event}')
                if entry.event == 'operation_succeeded':
                    saved = entry.details.get('response', {}).get('availabilityevent')
                    if isinstance(saved, dict) and type(saved.get('id')) is int:
                        print('  Saved creation: ' + event_summary(saved))
                        try:
                            observed = provider.get(saved['id'], change.wiw_user_id)
                            print('  Current original record: ' + event_summary(observed))
                        except WIWError as exc:
                            print(f"  Original record {saved['id']}: reason={exc.reason} http_status={exc.http_status} wiw_code={exc.wiw_code}")
                if entry.event != 'operation_started': continue
                operation = entry.details['operation']
                event_id = operation.get('event_id')
                if event_id is None: continue
                try:
                    provider.get(event_id, change.wiw_user_id)
                    state = 'present'
                except WIWError as exc:
                    state = ('absent (HTTP 404)' if exc.reason == 'http_error' and exc.http_status == 404
                             else f'unknown: reason={exc.reason} http_status={exc.http_status} wiw_code={exc.wiw_code}')
                print(f"  {operation['action']} event {event_id}: {state}")
            print('Read-only inspection. No WIW writes or request status changes.')
            return
        elif args.command == 'resume-verified-deletions':
            from .weekly_workflow import resume_verified_deletions
            from .wiw import WIWError
            actor = db.scalar(select(User).where(User.email == args.manager_email.lower(),
                User.role.in_(['manager','admin']), User.active.is_(True)))
            if not actor: raise SystemExit('Active manager or administrator required.')
            try:
                change = resume_verified_deletions(db, actor, args.id, WIW())
            except (ValueError, WIWError) as exc:
                raise SystemExit(str(exc))
            print(f'Request #{change.id}: {change.status}')
        elif args.command == 'reconcile':
            change = db.scalar(select(Change).where(Change.id == args.id).with_for_update())
            actor = db.scalar(select(User).where(User.email == args.manager_email.lower(), User.role.in_(['manager','admin']), User.active.is_(True)))
            if not change or change.status not in ['applying','needs_reconciliation'] or not actor:
                raise SystemExit('An unresolved request and active manager are required.')
            if len(args.note.strip()) < 10: raise SystemExit('Include a useful reconciliation explanation.')
            if change.action == 'weekly':
                from .weekly_workflow import reconcile_weekly
                try:
                    reconcile_weekly(db, actor, change, args.outcome, args.note, WIW())
                except ValueError as exc:
                    raise SystemExit(str(exc))
            else:
                current = WIW().read(change.wiw_user_id, change.read_start, change.read_end)
                change.status = 'reconciled_applied' if args.outcome == 'applied' else 'reconciled_not_applied'
                audit(db, change, actor, change.status, {'note': args.note, 'observed_state': current})
        db.commit()
    print('Done.')

if __name__ == '__main__': main()
