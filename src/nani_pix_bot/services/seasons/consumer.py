"""The seasons' consumer of the event log (services/events.py)."""

from loguru import logger
from sqlalchemy.orm import Session

from nani_pix_bot.services.events import LoggedEvent
from nani_pix_bot.services.seasons import coins, lifecycle, objectives, season_pass, xp


def on_event(session: Session, event: LoggedEvent) -> None:
    # XP and coins first: a closing season's last game still pays into it.
    found = xp.tagged(session, event)
    if found is not None:
        awarded = xp.pay(session, event, found)
        coins.pay(session, event, found)
        for player_id in sorted({award.player_id for award in awarded}):
            season_pass.sync(session, found.season, found.run, player_id)
    # Objectives before the season can finalize on its last game's end.
    _check_objectives(session, event)
    lifecycle.on_game_ended(session, event)


def _check_objectives(session: Session, event: LoggedEvent) -> None:
    """Objectives in their own savepoint: a broken objective discards only
    its own work, never the game's XP, coins and pass, and never blocks the
    season's lifecycle step after it."""
    try:
        with session.begin_nested():
            objectives.check(session, event)
    except Exception:
        logger.opt(exception=True).error(
            "season objectives failed on event {event_type} (row {event_id}); XP and coins kept",
            event_type=event.event_type.value,
            event_id=event.id,
        )
