"""The seasons' consumer of the event log (services/events.py)."""

from sqlalchemy.orm import Session

from nani_pix_bot.services.events import LoggedEvent
from nani_pix_bot.services.seasons import lifecycle, xp


def on_event(session: Session, event: LoggedEvent) -> None:
    # XP first: a closing season's last game still pays into it.
    xp.on_event(session, event)
    lifecycle.on_game_ended(session, event)
