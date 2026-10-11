"""The seasons' consumer of the event log (services/events.py)."""

from sqlalchemy.orm import Session

from nani_pix_bot.services.events import LoggedEvent
from nani_pix_bot.services.seasons import coins, lifecycle, season_pass, xp


def on_event(session: Session, event: LoggedEvent) -> None:
    # XP and coins first: a closing season's last game still pays into it.
    found = xp.tagged(session, event)
    if found is not None:
        awarded = xp.pay(session, event, found)
        coins.pay(session, event, found)
        for player_id in sorted({award.player_id for award in awarded}):
            season_pass.sync(session, found.season, found.run, player_id)
    lifecycle.on_game_ended(session, event)
