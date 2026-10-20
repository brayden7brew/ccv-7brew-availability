# WIW integration contract

**Weekly workflow update:** The active UI now accepts positive Sunday–Saturday weekly hours. See [WEEKLY-AVAILABILITY.md](WEEKLY-AVAILABILITY.md) for recurring complement events, multi-operation journaling, replacement history, and reconciliation. The single-event adapter remains in source for legacy contract tests; new requests use the weekly adapter.

Verified 2026-09-29 against the official [API documentation](https://apidocs.wheniwork.com/external/index.html). That page loads `monolith/docs-master.json`; this exact source was retrieved, inspected, and the relevant schema saved in `wiw-contract.json` with a SHA-256 digest of the full source.

| Operation | Official endpoint | Used by portal |
| --- | --- | --- |
| List | GET `/2/availabilityevents` | `user_id`, `start`, `end` query; response `availabilityevents` |
| Get one | GET `/2/availabilityevents/{id}` | Ownership/account verification; response `availabilityevent` |
| Create | POST `/2/availabilityevents` | Approved new event; response `availabilityevent` |
| Update | PUT `/2/availabilityevents/{id}` | Approved edit; response `availabilityevents` |
| Remove | DELETE `/2/availabilityevents/{id}` | Approved removal; response `success: true` |
| Exception | POST `/2/availabilityevents/{id}/exceptions` | Documented, deliberately not implemented in UI |

The request schema requires `start_time` and `type`. The adapter sends only documented fields: `account_id`, `user_id`, `type`, `start_time`, `end_time`, `all_day`, `notes`, and optional `recurrence`. `type` is 1 (unavailable) or 2 (preferred). Notes are capped at 160 characters. `end_time` is documented as optional for all-day events; the app still supplies an explicit next-midnight end. Datetimes include offsets. Recurrence uses the documented RFC 5545 string format (`FREQ=WEEKLY;COUNT=n`). ID, recurrence end, timestamps, and login ID are not copied into outbound payloads. There is no invented `location_id`, approval flag, weekday array, or bulk-replace operation.

The official security description documents server authentication using a developer `W-Key` header and email/password at `https://api.login.wheniwork.com/login`, followed by `Authorization: Bearer <token>` on calls. A workplace service user is selected with `W-UserID`. The portal intentionally accepts a pre-provisioned token via a secret environment variable so it does not store the service user password. Read the [official login service docs](https://apidocs.wheniwork.com/external/index.html?repo=login) when automating token provisioning separately.

Individual writes have no application-level retries and use a 20-second HTTP timeout. A weekly approval may make multiple journaled calls. Redirects are disabled to avoid forwarding credentials. API errors are reduced to safe status information; raw error bodies are not shown. Official documentation describes rate limits, including 403 responses; the portal reports upstream failures and leaves pre-dispatch requests pending. Errors after dispatch require reconciliation because external side effects cannot be rolled back transactionally.

Approval re-reads the affected date range and, for updates/removals, the target event. A changed snapshot closes the request as a conflict. Comparison is intentionally conservative, including the full returned event data. The audit stores original state at submission plus current state immediately before dispatch and the successful response. A recurring series may extend beyond the bounded comparison range; its event/rule data is retained, but this is not an account-wide backup.

A token authorized in a live customer account was not supplied. Contract verification and mocked HTTP tests do not establish that a particular subscription, service account, native-availability setting, or recurring-edit behavior works in that customer account. Complete the README’s live acceptance test before launch.

## Availability-read range limit

On 2026-09-29 the test-site diagnostic confirmed WIW rejects ranges over 95 days with HTTP 400 / code 2002. The adapter now splits longer reads into contiguous 90-day UTC windows and combines results by event ID. Every window enforces employee/account ownership. A failed window or changed duplicate aborts the entire read; partial state cannot reach approval. This does not limit the repeating weekly schedule or introduce an employee end date.


### Recurring availability handover and split-update recovery

WIW PUT may return both an edited original series and a new series. When capping
legacy availability, use a UTC `UNTIL` immediately before the approved start date,
not `COUNT`: WIW can restart a count at the split date. Verify each returned record
by GET and compare the complete finite occurrence set against the intended capped
series, including local-time DST behavior, notes, type, and duplicate occurrences.
Future-only legacy entries are deleted before new schedule creation; historical
fragments are retained and excluded from portal-owned event tracking. Read back the
complete replacement before marking a handover applied.

For an initial approval stopped on its first legacy update, where one original
series became exactly two and every pre-start-date occurrence is unchanged:

```sh
python -m app.cli repair-split-update --id 12 --manager-email brayden@rva7brew.com
```

This is a live repair, not an inspection. It checks approval, employee mapping,
future effective date, the operation journal, and current WIW state, then journals
an absolute-date repair plan and verifies the final result. Changed earlier hours,
prior recovery attempts, other unresolved writes, or an existing approved portal
timeline stop it without writes. Never force status to applied or rerun after an
uncertain repair; inspect the new journal first.

### Background manager approvals

The web decision endpoint commits `queued` and redirects to the requests list.
The existing `python -m app.mailer` worker consumes approvals even when email is
disabled. Deploy both the web service and worker against the same database and
WIW mode. The requests list refreshes while visible approvals are processing.

Workers lock employee then request, recheck manager permission and employee
mapping, and commit `preparing` before remote reads. Existing operation journals
and final WIW verification still govern success. Only `queued` requests are
claimed: interrupted `preparing` or `applying` requests require inspection, never
an automatic replay. Pre-write failures become `approval_failed`; ambiguous
writes remain `needs_reconciliation`. Use `inspect-request` to investigate before
choosing an existing recovery command. Do not reset statuses to force a retry.
