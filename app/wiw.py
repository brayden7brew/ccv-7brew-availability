"""Official 2026-09-29 contract: docs/wiw-contract.json. Never log headers/bodies."""
import httpx
import re
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo
from dateutil.parser import parse
from .config import settings

class WIWError(Exception):
    def __init__(self, message, *, reason='unknown', http_status=None, wiw_code=None):
        super().__init__(message)
        # Structured diagnostics never contain response bodies, headers, or tokens.
        self.reason = reason
        self.http_status = http_status
        self.wiw_code = wiw_code

class WIW:
    def __init__(self, transport=None):
        self.cfg = settings()
        self.transport = transport

    def call(self, method, path, *, payload=None, params=None):
        try:
            with httpx.Client(base_url='https://api.wheniwork.com', timeout=20,
                              transport=self.transport, follow_redirects=False) as client:
                response = client.request(method, path, params=params, json=payload,
                    headers={'Authorization': f'Bearer {self.cfg.wiw_token}',
                             'W-UserID': str(self.cfg.wiw_context_user_id)})
            if not response.is_success:
                # Preserve diagnostic codes without exposing upstream bodies or credentials.
                code = None
                explanation = ""
                try:
                    body = response.json()
                    if isinstance(body, dict) and type(body.get('code')) is int:
                        code = body['code']
                    if isinstance(body, dict):
                        message = body.get('error') or body.get('message')
                        if isinstance(message, str):
                            # Only the explanation, never the complete response or headers.
                            for secret in (self.cfg.wiw_token, self.cfg.wiw_developer_key):
                                if secret:
                                    message = message.replace(secret, '[REDACTED]')
                            message = re.sub(r'eyJ[\w-]+\.[\w-]+\.[\w-]+', '[REDACTED]', message)
                            message = re.sub(r'[\w.+-]+@[\w.-]+', '[REDACTED EMAIL]', message)
                            explanation = ' '.join(message.split())[:400]
                except ValueError:
                    pass
                suffix = f' (WIW code {code})' if code is not None else ''
                raise WIWError(f'WIW returned HTTP {response.status_code}{suffix}. ' + (explanation or 'Contact the portal operator.'),
                    reason='http_error', http_status=response.status_code, wiw_code=code)
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except httpx.TimeoutException as exc:
            raise WIWError('When I Work took too long to respond.', reason='timeout') from exc
        except httpx.HTTPError as exc:
            raise WIWError('Could not connect to When I Work.', reason='connection_error') from exc
        except ValueError as exc:
            raise WIWError('When I Work returned an invalid response.', reason='invalid_response') from exc

    def read(self, user_id, start, end):
        if self.cfg.wiw_mode == 'demo':
            return {'availabilityevents': []}
        try:
            first, last = parse(start), parse(end)
            zone = ZoneInfo(self.cfg.business_timezone)
            first = first.replace(tzinfo=zone) if first.tzinfo is None else first
            last = last.replace(tzinfo=zone) if last.tzinfo is None else last
            first, last = first.astimezone(timezone.utc), last.astimezone(timezone.utc)
            if last <= first:
                raise ValueError()
        except (ValueError, TypeError, OverflowError) as exc:
            raise WIWError('Invalid availability date range.', reason='invalid_date_range') from exc
        # WIW error 2002 confirms a maximum of 95 days per request. Use 90
        # elapsed days, sharing boundaries, so there are no gaps or DST overruns.
        # Range results can describe occurrences of the same recurring series.
        # Discover IDs here, then fetch saved events for stable approval snapshots.
        event_ids = set()
        cursor = first
        while cursor < last:
            boundary = min(cursor + timedelta(days=90), last)
            result = self.call('GET', '/2/availabilityevents', params={
                'user_id': user_id, 'start': cursor.isoformat(), 'end': boundary.isoformat()})
            events = result.get('availabilityevents')
            if not isinstance(events, list):
                raise WIWError('WIW returned an unexpected event list.', reason='invalid_event_list')
            for event in events:
                if (not isinstance(event, dict) or type(event.get('id')) is not int
                        or event.get('user_id') != user_id
                        or event.get('account_id') != self.cfg.wiw_account_id):
                    raise WIWError('WIW returned an unexpected account/user or event list.', reason='event_identity_mismatch')
                event_ids.add(event['id'])
            cursor = boundary
        # Fail the whole read if any saved event is missing or cannot be verified.
        # Approval repeats this read and compares all fields with the submitted
        # snapshot, so real edits still block a write.
        return {'availabilityevents': [self.get(key, user_id) for key in sorted(event_ids)]}

    def get(self, event_id, user_id):
        result = self.call('GET', f'/2/availabilityevents/{event_id}')
        event = result.get('availabilityevent', {})
        if (not isinstance(event, dict) or type(event.get('id')) is not int
                or event['id'] != event_id or event.get('user_id') != user_id
                or event.get('account_id') != self.cfg.wiw_account_id):
            raise WIWError('Availability event does not match the configured employee/account.',
                           reason='event_identity_mismatch')
        return event

    def apply(self, change):
        # Defense in depth: only the durable approved/dispatching state is writable.
        if change.status != 'applying' or change.manager_id is None or change.dry_run is not False:
            raise WIWError('Write denied: approved live dispatch required.')
        if self.cfg.dry_run or self.cfg.wiw_mode != 'live':
            raise WIWError('Write denied by environment settings.')
        path = '/2/availabilityevents'
        if change.action != 'create':
            path += f'/{change.event_id}'
        payload = None if change.action == 'delete' else {
            **change.proposed, 'user_id': change.wiw_user_id, 'account_id': self.cfg.wiw_account_id}
        result = self.call({'create': 'POST', 'update': 'PUT', 'delete': 'DELETE'}[change.action],
                           path, payload=payload)
        key = {'create': 'availabilityevent', 'update': 'availabilityevents', 'delete': 'success'}[change.action]
        if key not in result or (change.action == 'delete' and result[key] is not True):
            raise WIWError('WIW write response could not be verified.')
        return result

    def weekly_operation(self, change, operation):
        if (change.action != 'weekly' or change.status != 'applying' or change.manager_id is None
                or change.dry_run is not False or self.cfg.dry_run or self.cfg.wiw_mode != 'live'):
            raise WIWError('Write denied: approved live weekly dispatch required.')
        if operation['action'] == 'delete':
            result = self.call('DELETE', f"/2/availabilityevents/{operation['event_id']}")
            if result.get('success') is not True:
                # Some successful responses do not use the documented success flag.
                # Verify absence without ever issuing a second DELETE.
                try:
                    self.get(operation['event_id'], change.wiw_user_id)
                except WIWError as exc:
                    if exc.reason != 'http_error' or exc.http_status != 404:
                        raise WIWError('WIW deletion could not be verified.', reason='delete_unconfirmed') from exc
                else:
                    raise WIWError('WIW still returns the event after deletion.', reason='delete_unconfirmed')
                result = {'success':True, 'verified_by':'event_get_404'}
        elif operation['action'] == 'update':
            result = self.call('PUT', f"/2/availabilityevents/{operation['event_id']}", payload={
                **operation['payload'], 'user_id':change.wiw_user_id, 'account_id':self.cfg.wiw_account_id})
            events = result.get('availabilityevents')
            if (not isinstance(events, list) or len(events) != 1
                    or not isinstance(events[0], dict) or events[0].get('id') != operation['event_id']
                    or events[0].get('user_id') != change.wiw_user_id
                    or events[0].get('account_id') != self.cfg.wiw_account_id):
                raise WIWError('WIW did not confirm the expected availability update.')
            # A successful HTTP status alone does not prove that WIW capped the rule.
            observed = self.get(operation['event_id'], change.wiw_user_id)
            self.verify_payload(observed, operation['payload'])
        elif operation['action'] == 'create':
            result = self.call('POST', '/2/availabilityevents', payload={
                **operation['payload'], 'user_id':change.wiw_user_id, 'account_id':self.cfg.wiw_account_id})
            event = result.get('availabilityevent', {})
            if (not isinstance(event, dict) or type(event.get('id')) is not int or event.get('user_id') != change.wiw_user_id
                    or event.get('account_id') != self.cfg.wiw_account_id):
                raise WIWError('WIW created-event response could not be verified.')
            self.verify_payload(event, operation['payload'])
        else:
            raise WIWError('Unsupported weekly operation.')
        return result

    @staticmethod
    def verify_payload(event, payload):
        from .event_identity import event_signature
        try:
            matches = (event_signature(event) == event_signature(payload)
                       and ('notes' not in payload or event.get('notes', '') == payload['notes']))
        except (ValueError, TypeError, KeyError, OverflowError):
            matches = False
        if not matches:
            raise WIWError('WIW returned different dates, hours, or recurrence than approved. Stop and reconcile this request.',
                           reason='write_payload_mismatch')
