# Recovering a create-only availability conflict

Use `inspect-request --id ID` first. `resume-verified-creations` is an operator-only,
one-attempt recovery for an approved live weekly request that began with no WIW
availability, saved at least one creation, and stopped on an explicit HTTP 409 / WIW
4090 rejection. It never deletes or recreates a successful event.

After deploying the inclusive all-day endpoint encoding, run:

```sh
python -m app.cli resume-verified-creations --id ID --manager-email MANAGER_EMAIL
```

The command requires a future effective date, unchanged employee mapping and
permissions, a complete original operation journal, unchanged saved WIW creations,
no extra WIW events, and unchanged portal managed events and timeline. It resumes
at the rejected creation. Before marking the request applied it reads back and
compares the complete schedule. A rejection or failed verification leaves the
request unresolved; inspect it again rather than bypassing the guards.

The wire format now uses WIW's inclusive 23:59:59 end for a single all-day period;
the approved plan continues to use an exclusive next-midnight end. Both represent
the same local calendar day, including 23- and 25-hour daylight-saving days. This
addresses the endpoint representation; it does not establish that every upstream
409 is caused by that representation.

## Confirmed clock-change anchor conflict (2026-10-08)

Controlled tests on TEST BRAYDEN TEST reproduced the live failure: a weekly
all-day Sunday anchored November 1, 2026 followed by an adjacent Monday rule
returns HTTP 409 / WIW 4090. The expanded dates show no local overlap. Reversing
creation order and omitting the optional all-day end time did not avoid it.
Normal adjacent-day rules, including anchors on opposite sides of the clock
change, succeeded. Every test-created event was deleted and the original rules
were restored and verified.

The verified equivalent representation is a one-off November 1 event plus weekly
Sundays anchored November 8. Monday then succeeds. The planner now separates an
initial clock-change all-day occurrence, preserving finite counts and indefinite
future availability. Existing approved payloads are never silently rewritten.

For an original empty-state request with only the first DST-anchor creation saved
and explicit conflict rejections at operation 1, an operator may run:

```sh
python -m app.cli repair-dst-anchor --id ID --manager-email MANAGER_EMAIL
```

The repair checks permissions, mapping, future date, unchanged approval, complete
journal, saved event identity/content, the entire current WIW state and portal
managed state. It records a replacement operation plan, changes the existing
first event to one-off in place, creates the next-week recurring rule and missing
days, and verifies the complete final schedule. Any uncertainty stops recovery;
a second repair attempt is refused. Earlier availability is never edited by this
narrow empty-state recovery path.
