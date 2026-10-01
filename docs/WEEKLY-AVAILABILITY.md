# Weekly availability revision

The employee enters seven days in Sunday–Saturday order, with one daily window of positive available hours, all day, or no hours. There is a single effective/start date, which can be any day of the week. There is no end date, preference-type selector, or recurrence-count input. From/To are daily clock times, not dates. Midnight as the To time means the end of the day. Overnight availability is split between days.

Only completed approvals enter the approved timeline. Requests pending, rejected, stale/conflicted, or awaiting reconciliation do not supersede the previous approved schedule in the portal. Partial WIW writes can already have external effects; the status prominently reports that uncertainty. Approvals for the same start date replace the earlier version on that date, without erasing its stored approval/audit. Out-of-order future start dates are sorted chronologically. Stale pending requests need resubmission if another approval changed the timeline since submission.

Dry-run approvals maintain a separate simulated timeline. Switching DRY_RUN off cannot silently promote them into live schedules. A new request is required.

## WIW representation

The verified official AvailabilityEvent schema (`wiw-contract.json`) supports `type=1` unavailable and `type=2` preferred, not a plain available value. We emit unavailable intervals outside the entered available hours. For 09:00–17:00, these are midnight–09:00 and 17:00–next midnight. A day with no hours has one all-day unavailable event. An all-day available day needs no blocking event. This assumes unmanaged WIW preferences have been resolved first; the app refuses to silently delete them.

All generated events use the documented POST `/2/availabilityevents` fields: type, start_time, end_time, all_day, notes, recurrence, user_id, account_id. The latest profile uses `FREQ=WEEKLY` with no end. Earlier profiles use a per-weekday `COUNT` that ends before the next approved effective date. Dates carry the configured business time-zone offset. The API's account-timezone interpretation and daylight-saving recurrence behavior still require verification against the actual test account before enabling live writes.

The API schema requires start_time no more than 24 hours in the past. We do not guess whether a partial PUT could trim an old series without that field. Instead, the app owns a registry of generated WIW event IDs. On a new approval, it snapshots/validates those events, deletes only those owned events, then recreates the approved timeline from today's local midnight onward, including the bridge from current hours to the future start date. Recreated starts are no more than 24 hours in the past. Portal approval/audit history is retained permanently; historical occurrences of those old recurring series are removed from WIW when the series is replaced. This is an explicit integration tradeoff, not an atomic WIW schedule replacement.

Each operation is journaled before dispatch. Responses and new IDs are persisted after each success. A failure leaves `needs_reconciliation`; a crash may leave `applying`. Neither is retried automatically. Other approvals for that employee are blocked until reconciliation. The operator must stop in-flight dispatches, inspect the recorded plan, and manually complete or restore WIW as appropriate. The CLI compares the full expected event set before accepting `applied`; a partial batch is refused. Reconciliation itself only reads WIW.

Availability comparisons use a bounded range from submission day through one year after the requested start, plus direct GET verification of every owned event. Very distant unmanaged preferences outside that horizon cannot be exhaustively discovered by this range-based implementation. Native availability access should be disabled for ordinary employees and external manager edits coordinated.

## Upgrade

Run `alembic upgrade head`, then restart the server. The local test launcher does this automatically. Migration `d73b_weekly` adds approved weekly schedules and the managed-event registry without modifying previous request/audit records or user accounts. Old event-based pending requests must be resubmitted in the weekly form.

No live WIW availability was changed while implementing this revision. DRY_RUN remains controlled by the existing local configuration and is not switched off by the upgrade.

## Availability-read range limit

On 2026-09-29 the test-site diagnostic confirmed WIW rejects ranges over 95 days with HTTP 400 / code 2002. The adapter now splits longer reads into contiguous 90-day UTC windows and combines results by event ID. Every window enforces employee/account ownership. A failed window or changed duplicate aborts the entire read; partial state cannot reach approval. This does not limit the repeating weekly schedule or introduce an employee end date.


WIW live validation (2026-09-29): HTTP 400 / code 2006, “Choose a day to repeat on,” rejects FREQ=WEEKLY without an explicit weekday. The planner now supplies BYDAY=SU through BYDAY=SA, matching each event’s local start date. BYDAY uses RFC 5545 section 3.3.10 (https://www.rfc-editor.org/rfc/rfc5545). Live acceptance of the corrected payload remains to be verified. Regression suite: 77 passed, 1 skipped (Postgres-only).

### Reviewing existing WIW availability

Managers can explicitly confirm a dated handover of external WIW preferences on
an employee's request page. The saved submission snapshot is compared with WIW
again before any operation. External events ending before the effective date are
left alone; future-only events are deleted; recurring series crossing the date
are capped with COUNT using their original DTSTART. One-off events crossing the
boundary are shortened. Unsupported recurrence or overnight recurring boundary
cases are blocked for manual review, without writes.

The approval journal includes the full original state, exact operations, and
expected retained external entries. Updates use the documented event PUT and
are read back to verify all submitted fields. Any failure or unexpected split
stops processing and requires reconciliation; it is never retried automatically.
Retained external events are not registered as portal-managed, so later weekly
rebuilds cannot delete their earlier history. Reconciliation includes these
retained events in the expected final state.

Live WIW acceptance of an update with a historical DTSTART still needs a test
against the test workplace. If WIW rejects it under its documented start-time
restriction, the portal stops instead of deleting/recreating historical entries.

### Recovering a confirmed deletion prefix

`python -m app.cli resume-verified-deletions --id REQUEST_ID --manager-email ADMIN_EMAIL`
continues an existing approved live plan and DOES write to WIW. It only accepts
`needs_reconciliation` requests where every attempted operation was a delete,
every deleted ID now returns HTTP 404, and the complete remaining read window
matches the pre-write snapshot minus those IDs. It also checks employee mapping,
future effective date, portal timeline and remaining managed-event snapshots.
A recovery audit is committed before continuing at the first unattempted index.
It never repeats a deletion and refuses ambiguous create/update operations.
