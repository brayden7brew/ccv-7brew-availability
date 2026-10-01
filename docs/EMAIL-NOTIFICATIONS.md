# Email notifications

Sender: alerts@rva7brew.com. Sending is disabled until the mailbox connection and a public HTTPS portal URL are configured. A shared mailbox address alone does not provide authentication or permission to send as it. The provider is Microsoft 365. A Graph adapter is now available; follow MICROSOFT-365-EMAIL.md. The SMTP adapter remains available for other providers. Do not enable basic authentication or weaken tenant security just to use this adapter.

Once a provider-supported SMTP relay/account with permission to send as alerts@rva7brew.com is available, enter SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, SMTP_SSL and PUBLIC_BASE_URL in the private .env or deployment secrets. EMAIL_FROM is already set. Keep credentials out of chat and source control. SMTP_SSL=false requires STARTTLS with certificate verification; SMTP_SSL=true uses TLS on connect. Set EMAIL_ENABLED=true only after the configuration is complete, and restart the web app and worker. A localhost link cannot work for other employees.

Run the worker from the project directory with the same virtual environment and environment variables as the web app:

```zsh
source .venv/bin/activate
python -m app.mailer
```

For this Mac's existing installation use `../../work/venv/bin/python -m app.mailer` from the project directory. For Render, add a background worker using the same code, database and environment settings as the web service, with build command `pip install -r requirements.txt` and start command `python -m app.mailer`. Apply migrations first. Use PostgreSQL for production; SQLite is for sequential local tests, not concurrent web/worker use.

## Routing and delivery

- A new submission queues one notification per active manager/admin scoped to either of the employee's two snapshotted locations, excluding the requester. The subject includes the employee's name. The link opens the authenticated request detail, never a one-click approval.
- The employee receives the manager's approval or rejection and their note. An approval email explicitly distinguishes the manager's decision from WIW delivery; dry-run approvals are labeled as tests.
- Decision and email queue entries commit together. Email failure does not undo the decision. No historical emails are generated for events that occurred while email was disabled.
- In Admin, enter a real notification email for each person. Successful WIW login fills a missing notification address using the authenticated login email. Invalid/internal test addresses are not sent mail. A queued message with a missing address is held until the administrator supplies one.
- The worker rechecks active accounts and manager location access before dispatch, and drops stale pending-request notifications after a decision.
- Delivery retries up to five attempts with backoff. Admin shows queued/retry/sent/failed/missing-address counts. Errors contain exception types, never provider response bodies or secrets. SMTP acceptance does not prove inbox delivery. A crash after provider acceptance but before DB commit can result in a duplicate email; the message ID is stable. WIW writes are never retried by this worker.

SMTP client reference: https://docs.python.org/3/library/smtplib.html
