## WIW sign-in revision — 2026-09-29

Full suite: **74 passed, 1 PostgreSQL-only test skipped**. Added 19 authentication checks. Official login schema inspected in the browser; the actual local sign-in page renders successfully with the configured key. No real WIW passwords were submitted during these tests. See `WIW-LOGIN.md` for limitations.

## Date-range fix — 2026-09-29

The full suite passes with the 90-day read-window fix: 55 passed, 1 PostgreSQL-only test skipped. Tests cover a 400-day read, contiguous coverage, duplicate recurring events, later-window failures, wrong-owner events, changed duplicates, and invalid input ranges. The actual WIW 95-day limit was confirmed by the user-run diagnostic; live retest of the patched portal remains to be done.

# Weekly revision verification — 2026-09-29

- Updated suite: **47 passed, 1 skipped**. PostgreSQL-only concurrent decision test remains skipped locally.
- Positive Sunday–Saturday form, no end date/type/count fields; all-day/no-hours/midnight validation; perpetual latest recurrence; capped prior rules; midweek starts; chronological future schedules; same-start supersession; pending/rejected schedules leave approved history untouched.
- End-to-end fake-WIW test applies an initial weekly schedule and then replaces it, verifying saved original event snapshots, generated event IDs, capped recurrence, and approved history.
- Partial batch failure is journaled and blocks further approvals. Read-only reconciliation refuses an incomplete final week. Unmanaged WIW preferences are not silently deleted.
- The existing local test database was backed up to the workspace scratch area and migrated successfully. Alembic reports no schema drift. User logins and DRY_RUN=true were preserved.
- Updated form visually inspected at desktop and 390-pixel phone viewport using sample data. No WIW writes were made.
- Actual WIW weekly writes/recurrence/timezone behavior still need controlled acceptance testing before live mode. PostgreSQL CI has not been run remotely.

---

# Delivery verification — 2026-09-29

## Completed here

- Python 3.14.7 local environment; all pinned dependencies installed successfully.
- `pytest -q`: **29 passed, 1 skipped**. The skipped case requires a running PostgreSQL instance for concurrent decisions.
- Tests cover employee submission, manager location authorization, self/employee approval denial, rejection reasons, dry-run writes suppressed, duplicate decisions, state conflicts, pre-write snapshot persistence, successful/uncertain delivery, mapping changes, unresolved-write blocking, CSRF, logout/session revocation, expired/disabled sessions, login throttling, host/security headers, output escaping, date/type/recurrence validation, production configuration guardrails, and documented WIW HTTP methods/fields.
- Alembic upgrade → check (no model drift) → downgrade → upgrade succeeded on a disposable SQLite database.
- PostgreSQL offline migration SQL generated successfully, including the append-only audit trigger; included as `docs/postgresql-schema.sql` for inspection. Use Alembic for deployment, not manual execution of this reference file.
- `pip check`: no broken requirements.
- `pip-audit` checked every package pinned in `requirements.txt`: **no known vulnerabilities found** after upgrading the test runner. This is a point-in-time dependency check, not an independent penetration test.
- Local Uvicorn startup and login page verified in the browser; desktop login layout visually inspected. Responsive styles are included; full physical-device testing was not performed.
- Official WIW OpenAPI source retrieved and its availability and authentication schemas inspected before integration implementation.

## Remaining before production activation

1. Run the included GitHub Actions PostgreSQL job (or equivalent dedicated PostgreSQL test database). PostgreSQL is not installed in this local environment; runtime locking and audit-trigger behavior have not been exercised here. The CI workflow is included, not executed remotely.
2. Perform real WIW reads with verified account/user IDs and privileged service credentials.
3. Test the native Availability-off setting as an employee and confirm service-account API writes remain allowed for your workplace.
4. Exercise create, update, and delete against designated future availability in that account; compare returned fields and recurrence behavior, and preserve results in the audit. No live WIW calls/writes were made during this build.
5. Verify Render deployment, HTTPS cookies, database backups, ingress controls, and mobile employee/manager workflows. No Render resources were created.

The test client currently reports a deprecation warning about its httpx compatibility adapter. Tests pass; runtime WIW HTTP calls use httpx directly and are unaffected.

2026-09-30 admin/location and weekly-total update: 86 passed, 1 skipped (PostgreSQL-only test). Tests cover non-admin denial, CSRF, manager scope changes, disallowed administrator promotion, pending-request location changes, administrator cross-location approval denial, and weekly hour totals including midnight and half-hours. The local migration to e81_admin_audit completed. No WIW writes were performed for this update.

2026-09-30 two-location/admin/email update: 91 passed, 1 skipped (PostgreSQL-only). New coverage includes second-location manager visibility and approval, repeat-decision denial, five-admin cap, self-demotion and duplicate-location rejection, multi-location recipient deduplication, notification-off mode, approval/rejection bodies, email retry redaction, and revoked-manager cancellation. Tests use fake mail senders: no live mailbox delivery has been verified. Migration f92_locations_email applied locally.
