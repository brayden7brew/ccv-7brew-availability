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
