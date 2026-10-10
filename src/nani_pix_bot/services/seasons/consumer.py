"""The seasons' consumer of the event log (services/events.py)."""

from sqlalchemy.orm import Session

from nani_pix_bot.services.events import LoggedEvent
from nani_pix_bot.services.seasons import xp


def on_event(session: Session, event: LoggedEvent) -> None:
    xp.on_event(session, event)
