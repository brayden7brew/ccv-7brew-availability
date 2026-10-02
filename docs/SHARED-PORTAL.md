# Shared CCV team portal

The employee portal is the shared entry point at `https://a.rva7brew.com/`.
Existing availability accounts/passwords and WIW identities remain authoritative.
No users, approvals, schedules, or historical Ops data are deleted or remapped.

After signing in, users see cards for their permitted tools. Requests now live at
`/requests`; existing request-detail URLs are unchanged. The installed Home Screen
app opens the shared home page and uses the CCV Portal name/logo.

## Access

In **Admin → person → Tools this person can use**, enable Availability and/or Ops.
Ops access requires selecting at least one stand. Administrators can use every tool
and view all Ops stands. Approval scopes and notification preferences remain separate.
Every Ops page and data request checks the active database account and current grants.
There is no office-IP bypass in this shared portal.

Existing employees retain availability access. Ops starts off for non-admins.
Existing standalone Ops accounts stay in the old Ops service; they are not silently
matched by name or granted employee-portal access. Use the person's existing
availability login in the shared portal and grant Ops on their card. Anyone without
an employee-portal account must be explicitly enrolled/linked before using it.

## Deploy the connection

1. Deploy the Ops repository with its new `/api/integration/dashboard` endpoint.
2. Generate a random secret of at least 32 characters in a password manager. On the
   **Ops Render web service**, set `OPS_INTEGRATION_KEY` to that secret.
3. On **availability-portal**, set `OPS_BACKEND_URL=https://ops.rva7brew.com` and
   `OPS_INTEGRATION_KEY` to the exact same secret. Redeploy both web services.
4. Deploy this portal with `alembic upgrade head` (already the pre-deploy command).
   Migration `k07_portal_modules` adds access fields and preserves existing users.
5. Sign in as admin. Open Ops Dashboard and a stand; check live data appears. Grant
   a test employee one stand and verify other stand URLs return access denied.

The bridge is read-only and disabled without its secret. The secret is sent only
server-to-server over HTTPS, not in browser HTML, cookies, JavaScript or URLs.
Upstream redirects are rejected. Upstream stand filtering is repeated in the employee
portal. Failed upstream requests return a temporary-unavailable response, not fake
metrics. Ops continues owning its original database, reporting jobs and alerts.
No extra Xenial credentials or duplicate polling workers are needed.

The existing native iPhone Ops project and standalone Ops login are unchanged by this
web portal integration. The unified app currently uses the mobile Home Screen web app.

## Extending it

Add each future tool's server authorization and per-user grants, then its home card.
Do not use a hidden card as authorization. Keep module permissions separate from
roles, approval scopes, and notification preferences.

## Verification

Availability suite: 199 passed, one database-specific test skipped locally.
Browser scripts: 9 passed. Ops suite: 33 passed.
Desktop and 390px mobile layouts inspected with a local preview using synthetic data.
Production connection must be checked after the two matching secrets are configured.
