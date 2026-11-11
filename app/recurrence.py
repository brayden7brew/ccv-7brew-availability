"""Read WIW's inline EXDATE extension without changing its saved wire format."""
import re
from datetime import datetime, timezone
from dateutil.rrule import rrulestr, rruleset


def parse_recurrence(rule, *, dtstart):
    if not isinstance(rule, str) or '\n' in rule or '\r' in rule:
        raise ValueError('Invalid WIW recurrence.')
    parts = rule.removeprefix('RRULE:').upper().split(';')
    base, exclusions = [], []
    for part in parts:
        if part.startswith('EXDATE='):
            for value in part[len('EXDATE='):].split(','):
                # WIW's observed format is an absolute UTC occurrence timestamp.
                # Reject ambiguous dates rather than silently restoring a skipped day.
                if not re.fullmatch(r'\d{8}T\d{6}Z', value):
                    raise ValueError('Unsupported WIW exclusion date.')
                exclusions.append(datetime.strptime(value, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc))
        else:
            base.append(part)
    recurrence = rrulestr(';'.join(base), dtstart=dtstart)
    if not exclusions:
        return recurrence
    result = rruleset()
    result.rrule(recurrence)
    for excluded in exclusions:
        result.exdate(excluded)
    return result
