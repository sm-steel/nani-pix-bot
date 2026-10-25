"""Ofuda (Halloween season charm): armed for one round, forgives one wrong
guess there. Arming takes nothing; the Ofuda is spent only when it forgives."""

import enum
from datetime import datetime

from loguru import logger
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import ConsumableKind, EventType, GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.halloween import ArmedOfuda
from nani_pix_bot.services import events
from nani_pix_bot.services.cosmetics import consumables


class ArmRefusal(enum.StrEnum):
    NO_GAME = "no_game"  # not the ACTIVE game any more (stale button)
    OWN_GAME = "own_game"  # the starter can't guess, so can't arm
    HARD_MODE = "hard_mode"  # no per-stage limit to protect
    NONE_HELD = "none_held"
    ALREADY_ARMED = "already_armed"


class ArmRefusedError(Exception):
    def __init__(self, refusal: ArmRefusal) -> None:
        super().__init__(refusal.value)
        self.refusal = refusal


def is_armed(session: Session, game_id: int, player_id: int) -> bool:
    return session.get(ArmedOfuda, (player_id, game_id)) is not None


def _game_refusal(game: Game | None, player_id: int) -> ArmRefusal | None:
    if game is None or game.status != GameStatus.ACTIVE:
        return ArmRefusal.NO_GAME
    if game.starter_id == player_id:
        return ArmRefusal.OWN_GAME
    if game.hard_mode:
        return ArmRefusal.HARD_MODE
    return None


def refusal(session: Session, game: Game | None, player_id: int) -> ArmRefusal | None:
    game_reason = _game_refusal(game, player_id)
    if game is None or game_reason is not None:
        return game_reason
    if consumables.count(session, player_id, ConsumableKind.OFUDA) <= 0:
        return ArmRefusal.NONE_HELD
    if is_armed(session, game.id, player_id):
        return ArmRefusal.ALREADY_ARMED
    return None


def arm(session: Session, game: Game | None, player_id: int, now: datetime) -> None:
    reason = refusal(session, game, player_id)
    if game is None or reason is not None:
        reason = reason or ArmRefusal.NO_GAME
        logger.warning("Ofuda arm refused: {refusal}", refusal=reason.value)
        raise ArmRefusedError(reason)
    session.add(ArmedOfuda(player_id=player_id, game_id=game.id, armed_at=now))
    session.flush()
    logger.info(
        "armed an Ofuda ({held} held)",
        held=consumables.count(session, player_id, ConsumableKind.OFUDA),
    )


def forgive(session: Session, game: Game, player_id: int) -> bool:
    """On a wrong guess: spend the armed Ofuda so the miss doesn't count."""
    row = session.get(ArmedOfuda, (player_id, game.id))
    if row is None:
        return False
    armed_at = row.armed_at
    session.delete(row)
    session.flush()
    if not consumables.take(session, player_id, ConsumableKind.OFUDA):
        logger.warning("armed Ofuda had no Ofuda left to spend: the miss counts")
        return False
    events.emit(
        session,
        EventType.OFUDA_USED,
        events.Involved(actor_id=player_id, game_id=game.id),
    )
    logger.info(
        "an Ofuda forgave the miss: {left} left (armed {armed_at})",
        armed_at=armed_at,
        left=consumables.count(session, player_id, ConsumableKind.OFUDA),
    )
    return True
