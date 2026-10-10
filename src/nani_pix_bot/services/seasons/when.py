"""Admin-typed dates for /season (seasons spec §1): YYYY-MM-DD HH:MM in the
admin's own timezone, returned as UTC."""

import re
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from nani_pix_bot.services.quiet_hours import parse_hhmm

_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def parse_local(date_text: str, time_text: str, tz: ZoneInfo) -> datetime | None:
    match = _DATE.match(date_text.strip())
    clock = parse_hhmm(time_text)
    if match is None or clock is None:
        return None
    try:
        day = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None
    return datetime.combine(day, clock, tzinfo=tz).astimezone(UTC)
