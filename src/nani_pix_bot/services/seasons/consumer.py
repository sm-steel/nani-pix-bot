"""The seasons' consumer of the event log (services/events.py)."""

from loguru import logger
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import XpSource
from nani_pix_bot.services import players
from nani_pix_bot.services.events import LoggedEvent
from nani_pix_bot.services.seasons import (
    bones,
    coins,
    group_goal,
    hexes,
    lifecycle,
    objectives,
    season_pass,
    xp,
)


def on_event(session: Session, event: LoggedEvent) -> None:
    # XP and coins first: a closing season's last game still pays into it.
    found = xp.tagged(session, event)
    if found is not None:
        awarded = xp.pay(session, event, found)
        paid = coins.pay(session, event, found)
        _trigger_hexes(session, event, found, paid)
        if found.host_paid_unsolved and event.subject_id is not None:
            _log_host_top_up(session, found, event.subject_id, (awarded, paid))
        for player_id in sorted({award.player_id for award in awarded}):
            season_pass.sync(session, found.season, found.run, player_id)
    # Objectives before the season can finalize on its last game's end.
    _check_objectives(session, event)
    _season_mechanics(session, event)
    lifecycle.on_game_ended(session, event)


def _host_award(awards: list[xp.Award], host_id: int) -> int:
    return sum(a.amount for a in awards if a.source == XpSource.HOST and a.player_id == host_id)


def _log_host_top_up(
    session: Session,
    found: xp.Tagged,
    host_id: int,
    xp_and_coins: tuple[list[xp.Award], list[xp.Award]],
) -> None:
    """A /setwinner on a game that already paid the host the unsolved award:
    the win added only the difference, so the host ends with the solved one.
    Nothing added (a HARD MODE game pays no host; a run whose solved award is
    no more than the unsolved one) is nothing to log."""
    xp_awards, coin_awards = xp_and_coins
    added_xp, added_coins = _host_award(xp_awards, host_id), _host_award(coin_awards, host_id)
    if not added_xp and not added_coins:
        return
    logger.info(
        "topped the host up to solved: +{xp} XP, +{coins} 🪙 to {recipient}",
        xp=added_xp,
        coins=added_coins,
        recipient=players.describe_player_id(session, host_id),
        recipient_id=host_id,
        season_id=found.season.id,
        run_id=found.season.run_id,
        game_id=found.game.id,
    )


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


def _trigger_hexes(
    session: Session, event: LoggedEvent, found: xp.Tagged, paid: list[xp.Award]
) -> None:
    """Kappa/Kitsune in their own savepoint: a broken trigger leaves the hex
    open and never costs the game its XP, coins or pass."""
    try:
        with session.begin_nested():
            hexes.on_season_event(session, event, found, paid)
    except Exception:
        logger.opt(exception=True).error(
            "hex triggers failed on event {event_type} (row {event_id}); XP and coins kept",
            event_type=event.event_type.value,
            event_id=event.id,
        )


def _season_mechanics(session: Session, event: LoggedEvent) -> None:
    """The run's group goal and bone set, each in its own savepoint: a broken
    payout never costs the game its XP, coins or objectives, never costs the
    other mechanic its turn, and never blocks the season's lifecycle step
    after it."""
    for name, mechanic in (
        ("group_goal", group_goal.on_game_ended),
        ("bones", bones.on_game_won),
    ):
        try:
            with session.begin_nested():
                mechanic(session, event)
        except Exception:
            logger.opt(exception=True).error(
                "season mechanics failed ({mechanic}) on event {event_type} (row {event_id}); "
                "XP and coins kept",
                mechanic=name,
                event_type=event.event_type.value,
                event_id=event.id,
            )
