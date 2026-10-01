"""The single place every game/turn deadline is computed from "now" —
so quiet hours (services/quiet_hours.py) freeze all of them uniformly:
quiet time doesn't count toward any delay. With no quiet hours
configured this is exactly `datetime.now(UTC) + delay`."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from nani_pix_bot.services import quiet_hours
from nani_pix_bot.services.settings import bot_settings


def deadline_after(session: Session, delay: timedelta, *, now: datetime | None = None) -> datetime:
    """`now` (default: the current time) plus `delay` of non-quiet time.
    Pass `now` when the caller also stores that same instant alongside
    the deadline, so the two stay exactly `delay` apart outside quiet
    hours."""
    start = now if now is not None else datetime.now(UTC)
    return quiet_hours.add_active_time(bot_settings.get_quiet_hours(session), start, delay)
