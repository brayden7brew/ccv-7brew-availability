"""Plan a dated handover without deleting earlier WIW availability."""
from datetime import datetime, time
from zoneinfo import ZoneInfo
from dateutil.parser import parse
from dateutil.rrule import rrulestr
from .config import settings


def handover(events, effective):
    cutoff = datetime.combine(effective, time(), ZoneInfo(settings().business_timezone))
    operations, retained = [], []
    for event in events:
        try:
            start, end = parse(event['start_time']), parse(event['end_time'])
            if start.tzinfo is None or end.tzinfo is None or end <= start:
                raise ValueError()
            if event['type'] not in (1, 2):
                raise ValueError()
            payload = {k: event[k] for k in ('type','start_time','end_time','all_day','notes','recurrence') if k in event}
            rule = event.get('recurrence') or ''
            if not rule:
                if end <= cutoff:
                    retained.append(event)
                elif start >= cutoff:
                    operations.append({'action':'delete','event_id':event['id']})
                else:
                    payload['end_time'] = cutoff.isoformat()
                    payload['all_day'] = False
                    operations.append({'action':'update','event_id':event['id'],'payload':payload})
                    retained.append({**event, **payload})
                continue
            # Use the business timezone so weekly wall-clock hours survive DST.
            if '\n' in rule or '\r' in rule:
                raise ValueError()
            rule = rule.removeprefix('RRULE:').upper()
            recurrence = rrulestr(rule, dtstart=start.astimezone(cutoff.tzinfo))
            duration = end.astimezone(cutoff.tzinfo) - start.astimezone(cutoff.tzinfo)
            count = 0
            future = False
            for occurrence in recurrence:
                if occurrence >= cutoff:
                    future = True
                    break
                if occurrence + duration > cutoff:
                    raise ValueError('A repeating event crosses the start-date boundary. Review it in WIW first.')
                count += 1
                if count > 10000:
                    raise ValueError()
            if not future:
                retained.append(event)
            elif not count:
                operations.append({'action':'delete','event_id':event['id']})
            else:
                parts = [p for p in rule.split(';') if not p.startswith(('COUNT=', 'UNTIL='))]
                payload['recurrence'] = ';'.join(parts + [f'COUNT={count}'])
                operations.append({'action':'update','event_id':event['id'],'payload':payload})
                retained.append({**event, **payload})
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            raise ValueError('Existing WIW availability cannot be safely split at this start date. Review the event in WIW before approving.') from exc
    return operations, retained
