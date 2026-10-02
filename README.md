# Team Availability Approval

A FastAPI application for Brayden’s employee → manager → When I Work approval workflow. Mobile-friendly server-rendered pages, PostgreSQL, authenticated sessions, location-scoped review, before/after comparison, and append-only approval audit records. Local testing defaults to **DRY_RUN=true**.

Employees now enter a **Sunday–Saturday weekly schedule of hours they ARE available**, with one start date and no end date. Each day supports one time window, all day, or no hours. The schedule repeats indefinitely until the next approved schedule starts; pending/rejected requests have no effect. A new approval for the same start date supersedes the earlier version, which remains in audit history. See `docs/WEEKLY-AVAILABILITY.md` for integration and replacement details.

## Run on your Mac

Use Python 3.12 or newer. These commands assume you extracted the folder into Downloads; change only the first path if it is elsewhere.

```bash
cd ~/Downloads/availability-portal
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
python - <<'PY'
from pathlib import Path
import secrets
p = Path('.env')
p.write_text(p.read_text().replace('replace-with-at-least-32-random-characters', secrets.token_urlsafe(48)))
p.chmod(0o600)
PY
```

For PostgreSQL, install Docker Desktop if you do not already have it, start it, then:

```bash
docker compose up -d --wait db
alembic upgrade head
```

Alternatively, with Homebrew installed:

```bash
brew install postgresql@17
brew services start postgresql@17
/opt/homebrew/opt/postgresql@17/bin/createuser --pwprompt portal
/opt/homebrew/opt/postgresql@17/bin/createdb --owner=portal availability
```

Enter `portal` as the **local-only** database password to match `.env.example`, or change the URL to your chosen password. On an Intel Mac the Homebrew prefix is typically `/usr/local` instead of `/opt/homebrew`. Run `alembic upgrade head` after setup.

For a quick UI trial without installing a database, edit `.env` to set `DATABASE_URL=sqlite:///./portal.db`, then run `alembic upgrade head`. SQLite is a development convenience only; PostgreSQL is required for live concurrent use and production.

Create two local accounts. These are **portal** logins, separate from WIW. The commands securely prompt for passwords (12 characters minimum). Demo IDs below are examples; use actual WIW IDs before connecting to WIW.

```bash
python -m app.cli create-user --email employee@example.com --name 'Demo Employee' --role employee --wiw-user-id 1001 --location North
python -m app.cli create-user --email manager@example.com --name 'Demo Manager' --role manager --wiw-user-id 1002 --location North
python -m app.cli scope --email manager@example.com --location North
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. Sign in as the employee, enter a future start date and your available hours for Sunday through Saturday, and submit it. Sign out, sign in as the manager, and approve. The status becomes **approved dry run**. The demo availability remains empty because dry-run decisions never modify WIW or pretend that a change was applied. Reject another request with a reason to test that path.

Stop the server with Control-C. On future visits:

```bash
cd ~/Downloads/availability-portal
source .venv/bin/activate
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

## Features and deliberate boundaries

- Employees enter weekly available hours and view their current/upcoming approved schedules and request history. Managers compare weekly schedules side by side and approve or reject with notes.
- A different, location-authorized manager must approve. Rejections require a reason. A rejection or pending request does not end the existing weekly schedule.
- One start date, no end date or repeat count. Each day is a time window, all-day availability, or no hours. The From/To dropdowns offer 5:00 AM through 11:00 PM in 15-minute increments. All-day availability remains a separate choice. Existing saved schedules, including midnight end times, remain readable.
- Each approval rebuilds only portal-managed WIW availability events for today onward. Earlier snapshots and approved schedules remain in the portal audit. WIW itself may no longer show historical occurrences of replaced recurring series. The current pattern is capped before the next approved start date; the latest repeats indefinitely.
- WIW has unavailable/preferred events, not a plain available type. The adapter marks the complement of the submitted hours unavailable, leaving entered hours open. Employees never select WIW event types.
- Weekly writes are a journaled multi-call batch. Approval and each operation intent are committed before dispatch, with successful responses recorded individually. Any uncertain/partial batch blocks further approvals and requires read-only reconciliation. No automatic retries.
- Existing unmanaged WIW preferences are never silently deleted. A manager must resolve them outside the portal before submitting a fresh request. Native WIW settings and role controls remain essential.
- Dry-run approvals have a separate simulated timeline and never become live approvals when the setting changes. Older event-based requests remain readable/rejectable; employees must resubmit them using the weekly form.
- Portal locations are **authorization labels**, not availability API payload fields. WIW availability is associated with the user, not a location. For employees working multiple locations, choose one responsible approval group; coordinate authority across scheduling teams. Multiple groups can be assigned to each manager using repeated `scope` commands. Employee-specific management can use a distinct approval-group label for that employee.
- One WIW account per deployment. This avoids cross-account credential confusion. Add another deployment for a different workplace. The adapter is isolated in `app/wiw.py` for future expansion.
- Password reset, provisioning, disabling, and reconciliation are trusted operator CLI tasks. No public registration, email delivery, company SSO, or password-reset emails are configured. Optional WIW email/password sign-in is now supported for provisioned users; see `docs/WIW-LOGIN.md`.

## Connect WIW with real reads, still in dry run

Official API fields were verified on **2026-09-29** from [When I Work’s official documentation](https://apidocs.wheniwork.com/external/index.html) and its [linked OpenAPI JSON](https://apidocs.wheniwork.com/external/monolith/docs-master.json). The relevant contract and original source digest are in `docs/wiw-contract.json`. Details are in `docs/WIW-INTEGRATION.md`.

1. Obtain supported API access through WIW. Use a company-controlled supervisor/manager/admin account authorized for the employees involved.
2. Authenticate on the server using the official login service; obtain a token. The portal accepts that token through `WIW_TOKEN`; it does not collect WIW passwords or automatically refresh expired tokens. The operator must rotate the token on expiry/revocation. Never put a token in a browser, URL, source repository, screenshot, or support log.
3. Set `WIW_MODE=live`, `WIW_TOKEN`, `WIW_CONTEXT_USER_ID` (the service user within the intended workplace), and `WIW_ACCOUNT_ID`. Keep `DRY_RUN=true`.
4. Provision portal users with verified WIW **user IDs**, not person/login IDs. Assign their portal approval groups and manager scopes. Avoid reusing demo users with fictional mappings.
5. Restart the app and verify the employee’s current availability matches WIW. Approve a dry-run request and inspect its audit. The API adapter rejects returned events from another account or employee.
6. Perform a controlled live acceptance test with a designated future event and authorized manager. Only then set `DRY_RUN=false` and restart. Previously dry-run-approved requests stay dry run forever; submit a new request for live application.

There is no WIW credential supplied with this project. Real account permissions, subscription/API access, token lifecycle, and live API behavior require this acceptance test before a production rollout.

## Prevent bypass through native WIW editing

WIW’s [Scheduling Settings](https://help.wheniwork.com/articles/scheduling-settings/) explicitly says turning **Availability** off prevents employee-level users from entering availability preferences, while supervisors, managers, and admins can still view and set employee preferences. In WIW, open **Scheduling Settings → Availability**, turn it off, and save. Keep ordinary employees at employee-level access and tell them to use this portal.

The portal cannot change WIW’s native permission model: elevated WIW users can still edit directly, and settings may depend on your plan. Verify with a real employee account on web and mobile that native editing is disabled, then verify the service account can still write through the API. Official documentation supports the role distinction; this specific account-level API behavior has not been exercised without your credentials. Use WIW roles and internal policy to govern elevated users. The portal detects changed WIW state before applying approvals but cannot make an atomic compare-and-swap across WIW and PostgreSQL; restrict concurrent direct WIW edits operationally.

## Render deployment

The supplied `render.yaml` provisions a paid web service and PostgreSQL database. No deployment or paid resource has been created. See [Render’s Blueprint reference](https://render.com/docs/blueprint-spec).

1. Put this **project folder’s contents at your repository root**, including `render.yaml`, and push to your private Git repository.
2. Create a Render Blueprint from that repository. Review the displayed resource plans/costs.
3. Supply prompted WIW values and `ALLOWED_HOSTS` with your exact Render hostname (and custom hostname if used), separated by commas with no spaces or URL scheme. `SECRET_KEY` is generated by Render. Keep `DRY_RUN=true` for acceptance testing.
4. Render installs requirements, runs `alembic upgrade head` before deployment, then starts Uvicorn. HTTPS is terminated at Render; secure cookies and HSTS are enabled. Proxy headers are trusted only because the Render service sits behind Render’s ingress; do not copy this trust setting to a directly exposed server.
5. In the Render service Shell, run the `create-user` and `scope` commands above with real IDs and strong unique passwords.
6. Complete the acceptance checks above. Turn `DRY_RUN=false` only when ready and restart/redeploy.
7. Enable database backups/restore testing, restrict database network access, and monitor `/healthz`, failed sign-ins, and unresolved requests. Production startup rejects SQLite, demo mode, insecure cookies, or wildcard allowed hosts.

## Operations and uncertain writes

A timeout or lost response can mean WIW applied a write even though the portal could not confirm it. Such requests become `needs_reconciliation`. If the process stops after durable approval, a request may remain `applying`. **Neither state is automatically retried.**

```bash
python -m app.cli unresolved
```

Before reconciliation, stop serving the app / stop the old process and ensure no dispatch is still in progress. Inspect the request audit and WIW’s current state for the exact employee/date/event. Account for the action actually applied, including newly assigned event IDs. For weekly requests, complete or restore the entire saved WIW operation plan manually first. The reconcile command checks the full final event set before closing an applied weekly request and updating the portal timeline. A partial or extra event set is refused. Record the evidence in the reconciliation note:

```bash
python -m app.cli reconcile --id 42 --manager-email manager@example.com --outcome applied --note 'Verified every event in the approved weekly operation plan against WIW.'
```

Use `--outcome not-applied` only after verifying no write occurred. That closes the old request without retrying; a new employee submission and approval are required. Reconciliation reads and records current WIW state; it never writes WIW. Operator access is privileged, and the named manager is the attesting actor.

```bash
python -m app.cli reset-password --email employee@example.com
python -m app.cli disable-user --email employee@example.com
python -m app.cli scope --email manager@example.com --location North --remove
python -m app.cli cleanup
```

Password resets and disabling revoke existing sessions. Run cleanup daily to remove expired sessions and old login-attempt counters. Back up the database; it contains sensitive employee notes and audit history. PostgreSQL audit triggers reject row updates/deletes; database owners can still disable triggers, so restrict owner access. Use separate migration and runtime database roles for stricter privileges. Do not edit user mappings underneath pending requests; disable the old portal account and provision corrected mappings under operator supervision.

## Configuration

| Variable | Purpose |
| --- | --- |
| `SECRET_KEY` | Random session-signing secret, at least 32 characters. Rotating logs everyone out. |
| `DATABASE_URL` | PostgreSQL SQLAlchemy URL; Render `postgres://` URLs normalize automatically. |
| `ENVIRONMENT` | `development` or `production`. |
| `ALLOWED_HOSTS` | Comma-separated exact hostnames. |
| `SECURE_COOKIES` | `false` on local HTTP; `true` for production HTTPS. |
| `DRY_RUN` | Default `true`; suppresses all WIW writes. |
| `WIW_MODE` | `demo` (empty sample reads) or `live` (real WIW reads). |
| `WIW_TOKEN` | Server-only company bearer token; operator rotates it. |
| `WIW_DEVELOPER_KEY` | Optional server-only developer key enabling WIW email/password login. |
| `WIW_CONTEXT_USER_ID` | Service account user ID for `W-UserID` header. |
| `WIW_ACCOUNT_ID` | Target workplace ID. |
| `BUSINESS_TIMEZONE` | IANA time zone, default `America/New_York`. |
| `MINIMUM_NOTICE_DAYS` | Earliest allowed proposal, minimum one day. |
| `SESSION_HOURS` | Absolute session lifetime, default eight hours. |

Security includes Argon2 password hashes, server-side revocable sessions (only opaque session ID/CSRF token in the signed cookie), HttpOnly/SameSite cookies, CSRF protection including login, host checks, no inline scripts, escaped templates, generic upstream errors, and database-backed sign-in rate limiting by email and client IP. For an internet-scale service add ingress request-size/rate limits. No application endpoint returns WIW credentials.

## Tests

```bash
python -m pytest -q
# Browser-form logic checks (Node.js 22+):
node --test tests/weekly-form.test.cjs
```

Default tests use an isolated in-memory SQLite database and HTTP mocks; they never contact WIW. To test PostgreSQL, create a **dedicated disposable test database**, then:

```bash
TEST_DATABASE_URL=postgresql+psycopg://portal:portal@localhost:5432/availability_test python -m pytest -q
```

Tests create and drop application tables in that test database. Never point `TEST_DATABASE_URL` at production or a database you want to retain. The GitHub Actions workflow supplies PostgreSQL 17, validates migrations, and runs the suite. See `docs/TEST-REPORT.md` for checks actually run during delivery and remaining acceptance checks.

## Brayden’s configured local test-site launcher

`start-test-site.command` runs `scripts/setup_test_site.py`. It privately prompts for the WIW token and new local passwords for `blake@portal.test` and `brayden@portal.test` (local-only login labels, no email sent). It selects account 4319477, service user 53517822, and employee 53634927. It creates a separate local SQLite test database, enforces dry run, migrates it, assigns the manager scope, and starts localhost port 8000. Existing mismatched settings are refused. This is a sequential local workflow test; PostgreSQL remains required for production and concurrency validation. Run it from Terminal with `zsh start-test-site.command` after installing dependencies.

### Authorized live test-site run

When the local `.env` has `DRY_RUN=false`, restart using `zsh start-test-site.command --live`. The launcher still verifies test account 4319477 and service user 53517822. The UI displays a live-mode banner. Create a new employee request and approve it as the manager; prior dry-run requests are not promoted. This SQLite-based local test is for one operator testing sequentially, not concurrent production use. Set `DRY_RUN=true` and restart to return to simulation.

## Optional WIW sign-in

Set `WIW_DEVELOPER_KEY` in `.env` (or as a secret environment variable in Render), restart, and use **Sign in with When I Work**. Use actual WIW credentials. Employees must already have an enabled portal account mapped by WIW user ID. Manager permissions remain locally assigned. Existing portal logins remain available. This form does not support MFA or company SSO; see `docs/WIW-LOGIN.md` for the documented contract, security model, and acceptance-test limits.


### Rolling request limit
Set `AVAILABILITY_REQUEST_LIMIT_30_DAYS=2` in `.env` to allow two weekly submissions per employee over the preceding 30 days. Default `0` means unlimited. Restart the server after changing it. Pending, rejected, approved, and failed-sync submissions all count; invalid/blocked submissions do not. Each submission expires from the count exactly 30 days later. This is a submission limit, not a limit on manager decisions, and applies to all portal users submitting their own hours. Counts appear on the dashboard, form, and request detail. PostgreSQL employee-row locking serializes submissions to enforce the limit; SQLite remains for sequential local tests only.

### Administrator menu and location approvals
Administrators can open `/admin` to assign employee/manager roles, set each employee's approval location, and replace a manager's permitted approval locations (one exact location name per line). This is portal routing, not a WIW schedule assignment. New WIW sign-ins appear after automatic enrollment; set their correct location before they submit. A pending or unconfirmed request blocks moving an employee to a different location. Access changes are logged in `admin_audit`. This menu does not create additional administrators, reactivate disabled users, or alter WIW permissions.

Administrators also require an explicit approval scope to approve a location's requests. Managers and administrators save their own availability without additional approval; employees still require approval. Demoting a manager to employee clears their scopes. The administrator can bootstrap another administrator using the operator CLI; do not give employees CLI/server access. Run `python -m alembic upgrade head` when deploying this version.

Request comparisons and weekly schedule summaries now show available and unavailable totals over a nominal 168-hour Sunday–Saturday week. Midnight end times count as end of day, and totals include nights; they are not scheduled hours or store-opening hours.

WIW compatibility: the official AvailabilityEvent schema saved in `docs/wiw-contract.json` identifies `account_id` and `user_id`, with no schedule/location target. Therefore this integration does not support distinct availability by schedule or job site within one workplace. Portal locations control approval routing only; approved availability applies across that employee's workplace. Separate WIW workplaces have separate memberships. References: https://apidocs.wheniwork.com/external/index.html and https://help.wheniwork.com/articles/setting-your-availablity/ .

### Updated people and location menu
The Admin menu now uses a role dropdown, primary and optional second location dropdowns, and multiple approval-location checkboxes. Add choices under “Manage location choices.” These are portal routing labels, not automatic WIW schedule assignments. Only the primary location's scoped managers receive and decide employee requests. A secondary location does not grant request access or notifications. Request locations are saved at submission; pending/unconfirmed requests block changing those employee mappings. Administrators remain subject to approval scopes when deciding other employees’ requests. Managers and administrators save their own availability directly.

Up to five accounts may have administrator roles (including disabled accounts). Existing administrators can promote or demote other users; they cannot demote themselves. PostgreSQL serializes admin-role changes through a shared row lock. Operator create-user also enforces the cap.

Email setup, worker deployment, delivery behavior and shared-mailbox requirements: [EMAIL-NOTIFICATIONS.md](docs/EMAIL-NOTIFICATIONS.md). The sender is alerts@rva7brew.com; email is not connected or enabled yet.

### Import employees before their first sign-in

On the Admin page, choose **Import employees from WIW**. This reads the documented
`GET /2/users` endpoint using the configured workplace credentials. Only active,
non-deleted users whose `account_id` matches `WIW_ACCOUNT_ID` are imported. New
accounts get employee access and `WIW_AUTO_ENROLL_LOCATION`; WIW roles are not
copied. Existing accounts, disabled status, roles, locations and notification
addresses are preserved. Each new account is recorded in the access audit.
Repeated imports do not duplicate users. No WIW writes or notification emails
are triggered. Imported users sign in through WIW; no local password is created.
This is an add-only import, not ongoing offboarding synchronization.

### Remember this device
Both sign-in forms offer an unchecked “Remember me on this device for 60 days”
option. It persists the portal session cookie and sets a fixed 60-day server-side
expiry from sign-in. Without it, the configured `SESSION_HOURS` lifetime applies
(default eight hours). No WIW password or token is stored in the cookie. Logout,
operator password reset, and disabling an account revoke access as before.

### Enable separate portal login for an imported employee
In Admin, expand **Enable portal login / reset password** beneath the employee.
Confirm their login email and choose **Create setup code**. When email is enabled, leave the email checkbox selected to send the setup page
and code to the employee. You can also share them directly.
The employee enters the code at `/setup` and chooses a password (8+ characters).
Codes expire after 24 hours, work once, and are replaced by a newly issued code.
Only their hash is stored; they are never placed in URLs or the audit trail.
The email/password changes only when the employee completes setup. Their WIW ID,
role, locations, and notification email are preserved. Existing portal sessions
are revoked on completion. Disabled accounts cannot redeem codes, and a credential
change invalidates outstanding codes. This fallback does not inherit WIW MFA.
Run the latest Alembic migration before using this feature; Render's predeploy
command runs it automatically.

### WIW schedules populate location choices
Admin → **Import schedules from WIW** reads the documented `GET /2/locations`
endpoint. **Import employees & schedules from WIW** imports both in one database
transaction. Active schedule names from the configured workplace become choices
in both employee location dropdowns and manager approval checkboxes. Wrong-account
responses abort the import. Imports do not change employee assignments or grant
manager permissions. Existing names are retained for historical requests and
current permissions; schedule renames add a new choice and need administrator
review of affected assignments. Availability remains workplace-wide in WIW.

### Employee directory
Admin lists 25 people per page with server-side name/email search and filters
for primary or secondary location, portal role, and active/disabled status.
Select a name to open that employee's access and password-setup card. Back links
and saving access preserve directory filters. Import/email controls and the
recent access log are grouped into collapsible sections beneath the directory.

### Per-employee availability rules
Each employee card has **Availability requirements**. Defaults are a 15-hour
weekly minimum, counting only the overlap with 05:00–23:00 each day in the
business timezone, capped at 10 counted hours per day (including all-day availability), and 14 calendar days of advance notice. Longer availability remains saved in full; the cap only affects credit toward the minimum. Each rule has its own
on/off control. Minimum hours can be edited in quarter-hour increments (up to
70 hours), and notice can be 1–366 days. Turning notice off allows tomorrow,
not past/today start dates. The older MINIMUM_NOTICE_DAYS environment variable
no longer controls weekly submissions.

Existing users receive these defaults through migration, without a new-hire
exception. Newly created portal records (WIW auto-enrollment, roster import,
webhook new hire, or operator creation) can make their first successful weekly
submission for tomorrow. Invalid/failed submissions do not consume this exception;
a successfully submitted request does, including one later rejected or tested in
dry-run. All subsequent submissions use their configured notice. Minimum hours
still apply to the first request. Rules are validated under the employee lock
before reading WIW, are snapshotted in the submission audit, and never alter
already-submitted requests. Webhook updates preserve these individual settings.

### Live form validation
The weekly form requires JavaScript and validates dates, daily time windows, counted minimum hours, and request limits before enabling Send. It shows a live counted-hours total and errors beside daily time fields. Only a local script is allowed on the two weekly-form routes; inline and third-party scripts remain blocked. Server validation remains authoritative, including updated employee rules, and returns invalid submissions on the same form with entered values preserved. A WIW read failure also preserves the form without creating a request.

Administrators can use **Admin → My notifications** to disable their own automatic availability emails or select locations. These settings do not change approval access or anyone else’s notifications. Manager request emails require a matching approval location, checked both when queued and before delivery. Explicitly requested setup/test emails are separate from automatic availability notifications. Deploys run the notification-preferences migration automatically.

## Shared team portal

The home screen now includes Availability, Requests, Ops Dashboard and Administration
according to the signed-in person's permissions. Configure the private Ops reporting
connection and grant access as described in [Shared portal setup](docs/SHARED-PORTAL.md).
