# Connect alerts@rva7brew.com

The Microsoft Graph adapter is implemented and mock-tested, but has not been connected to your tenant. Email remains disabled. It uses application authentication, not a shared-mailbox password. Microsoft administrator setup is required.

1. In Microsoft Entra, register a single-tenant application for CCV 7 Brew Availability. Record the directory (tenant) ID and application (client) ID. Create an application credential and keep its value in deployment secrets; the implemented adapter uses a client secret. Set a rotation reminder in your normal credential-management process.
2. Have the Exchange administrator grant the application's service principal the `Application Mail.Send` role scoped only to alerts@rva7brew.com using Exchange Online RBAC for Applications. Test the mailbox scope. Do not combine this with unrestricted Entra Mail.Send permissions: those grants are additive and can defeat the intended restriction. This application does not need to read your mail.
3. Set these private environment variables:

```env
EMAIL_PROVIDER=microsoft_graph
EMAIL_FROM=alerts@rva7brew.com
MICROSOFT_TENANT_ID=<tenant UUID>
MICROSOFT_CLIENT_ID=<application UUID>
MICROSOFT_CLIENT_SECRET=<secret value>
PUBLIC_BASE_URL=https://<your deployed portal hostname>
EMAIL_ENABLED=false
```

4. Complete the public HTTPS deployment and verify recipient notification addresses in Admin. Then enable email and run the worker described in EMAIL-NOTIFICATIONS.md. New notifications will be queued. Confirm a test email arrives before rolling out. SMTP settings are not needed for microsoft_graph.

Graph returns 202 when it accepts a send; that is not proof of inbox delivery. As with SMTP, an interrupted response can lead to a duplicate notification on retry. Graph messages are retained in the sender's Sent Items by default.

Official references:
- https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0
- https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-client-creds-grant-flow
- https://learn.microsoft.com/en-us/exchange/permissions-exo/application-rbac
