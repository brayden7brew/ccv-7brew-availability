# Sign in with WIW

Optional employee/manager login is enabled when `WIW_DEVELOPER_KEY` is set in the server environment. It is separate from `WIW_TOKEN`, which remains the company service token used for availability reads and approved writes.

Visit `/login` and choose **Sign in with When I Work**, or open `/login/wiw`. Use the person's real WIW email/password, not the local `@portal.test` alias. Existing portal-password login remains available.

## Verified contract

The official [Authentication API 1.9.1](https://apidocs.wheniwork.com/external/index.html?repo=login#tag/Authentication/paths/~1login/post) was inspected in the browser on 2026-09-29. `POST https://api.login.wheniwork.com/login` takes JSON `email` and `password`. Its 200 example contains `person.id`, `person.token`, and top-level `token`. The [official access guide](https://help.wheniwork.com/articles/getting-access-to-the-when-i-work-api-computer/) documents the developer `W-Key` header and then `GET https://api.wheniwork.com/2/login` with `Authorization: Bearer <personal token>` to list that person's workplace memberships. The primary User schema documents `id`, `account_id`, `login_id`, `activated`, and `is_deleted`.

The portal requires exactly one active/non-deleted membership whose account ID matches the configured workplace and login ID matches the returned person. It then matches that workplace user ID to an existing enabled portal user. It never matches by email alone, auto-creates users, or grants a manager role from submitted or remote profile data. Manager approval scopes remain explicitly configured by the portal operator. Provision additional people through the existing create-user/scope commands with their verified WIW user IDs before their first WIW sign-in.

## Credential handling

Passwords and personal tokens are used only for those two server-side authentication calls, never stored in the database, audit, browser cookie, or application logs. A normal opaque portal session is issued afterward. The company service token is not replaced by an employee token. Requests have timeouts, no automatic authentication retries, and no redirects. Upstream error bodies are never echoed to users. Both login methods use CSRF protection and shared database-backed email/IP throttling.

Workplace membership is checked at each WIW sign-in. Existing sessions expire after the configured session lifetime; disabling a portal account denies further session use. Use HTTPS in production and do not enable request-body/header logging at the proxy or application layer.

## Two-step verification and SSO

The public login schema inspected here documents only email/password and a successful response. It does not specify MFA challenge submission or a hosted OAuth authorization flow. This implementation does not attempt to guess or bypass them. Unsupported/incomplete sign-in is refused with a message to use the separate portal login or contact the administrator. Do not disable two-step verification to use this portal.

## Validation

Mocked tests verify login payload/header separation, both documented token locations, cross-workplace/identity rejection, inactive/deleted memberships, incomplete/challenge-like responses, redirects, CSRF, throttling, provisioned-user lookup by WIW ID, disabled/unprovisioned access denial, preservation of roles/password hashes, and no secrets in browser output/cookies. The new page was verified on the running local portal. Actual credential-based WIW sign-in still needs the account owner's acceptance test; no employee password was supplied or used during implementation.

## Automatic employee access
`WIW_AUTO_ENROLL=true` enables first-login enrollment after WIW confirms an active, non-deleted membership in `WIW_ACCOUNT_ID`. Set `WIW_AUTO_ENROLL_LOCATION` to the exact portal location covered by the reviewing managers. This option assigns that location to all newly enrolled employees; for workplaces needing separate location routing, configure that mapping before enabling enrollment there. Existing users keep their roles and locations. Disabled portal accounts remain blocked. Enrollment never grants manager permissions or links by submitted email. New accounts have an internal identifier email and no usable local password; employees continue signing in with WIW. Restart after changing environment settings.

2026-09-30 MFA verification: re-read the official Authentication API 1.9.1 at https://apidocs.wheniwork.com/external/index.html?repo=login . The documented login request exposes email/password, but no MFA challenge verification endpoint or code field. User testing triggered SMS and could not complete portal login. Do not invent an OTP endpoint or disable WIW MFA. Existing users can use the separate portal login after an operator provisions a local password; that login has its own authentication policy and does not inherit WIW MFA. Direct WIW MFA support requires a supported challenge flow from WIW. The official MFA reference is https://help.wheniwork.com/articles/two-step-verification-technical-reference/ .
