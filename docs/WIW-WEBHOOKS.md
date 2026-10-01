# Automatic new-hire and employee updates

Official contracts reviewed October 1, 2026:
- https://help.wheniwork.com/articles/webhooks-reference/
- https://apidocs.wheniwork.com/external/index.html?repo=webhooks&branch=main

Technical callback documents `X-Signed-Hmac-256` as base64 HMAC-SHA256 of
raw request bytes using the signing secret, `X-Account-Id` as workplace ID,
and `X-Webhook-Id` as batch UUID. The affected employee is `data.userId`;
top-level `userId` is the actor, NOT the employee. Receiver accepts the live-confirmed `{ "events": [...] }` batch envelope,
one event, or an array of events. Only users::created/updated/deleted/invited are processed.

## Enable on the test workplace
1. Deploy this release (including automatic database migration).
2. In WIW webhook settings, use `https://a.rva7brew.com/webhooks/wiw`.
3. Subscribe to **Users** and save. Preserve any unrelated existing endpoints.
4. Copy WIW's generated signing secret into **availability-portal → Environment →
   WIW_WEBHOOK_SECRET** in Render. Save and redeploy. Do not put it in Git/chat.
5. The existing email worker also processes employee events, every 30 seconds.
   Both services must deploy this release. It needs no webhook secret itself.
6. Change a test employee name/schedule in WIW, then check Admin's Automatic
   employee updates and access history. No availability writes are performed.

New active employees get employee-role portal accounts mapped by WIW ID.
Current WIW names and up to two assigned active schedules are imported.
Existing role, approval scopes, local login email/password, and notification
email are preserved. Zero, unknown, or >2 schedules require admin review; new
accounts in that case have no approval location. Existing requests pending or
needing reconciliation defer location changes and record a review entry.
Administrators must resolve these and assign locations manually; there is no
silent choice of which schedules to discard.

Deleted/deactivated records returned by the WIW API disable portal access and
revoke sessions/setup codes. A portal-disabled account is never auto-reactivated.
If WIW cannot return the record (including a deleted-record 404), the batch is
retried and eventually flagged failed; an operator must review/disable access.
Do not treat receipt as proof synchronization has completed.

Receiver verifies signatures and workplace before saving only employee IDs and
body digest. No complete webhook HR payload is stored. Duplicate bodies return
success without requeuing. Worker reads current WIW records rather than applying
stale event deltas, and rolls back an entire failed batch. After eight attempts
it marks failed; Admin shows queue counts. To requeue after correcting the cause,
issue a fresh WIW user update. Import buttons remain available for new users and
schedule choices. They do not update existing profiles or perform offboarding.
