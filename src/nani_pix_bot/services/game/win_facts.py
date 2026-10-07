"""What a win looked like, for its `game_won` event (spec §1): every fact an
achievement needs, computed once while the game row still holds it. Called
from state._record_win before the pot is paid out."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import PixelStage, WinMethod
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.services.economy import bounty
from nani_pix_bot.services.game import guesses
from nani_pix_bot.services.settings import stage_config

_STAGES = list(PixelStage)


def stage_number(game: Game) -> int:
    """1-5 for a normal game, the turn (1-2) for HARD MODE; 0 if unknown."""
    if game.hard_mode:
        return game.hard_mode_turn or 0
    return 0 if game.current_stage is None else _STAGES.index(game.current_stage) + 1


def _seconds_live(game: Game, now: datetime) -> float | None:
    if game.activated_at is None:
        return None
    activated = game.activated_at
    if activated.tzinfo is None:
        activated = activated.replace(tzinfo=UTC)
    return (now - activated).total_seconds()


def _on_last_slot(session: Session, game: Game) -> bool:
    if game.hard_mode or game.current_stage is not PixelStage.STAGE_5:
        return False
    limit = stage_config.get_stage_config(session, game.current_stage).wrong_guess_limit
    return limit - game.wrong_guess_count == 1


def _wrong_guesses(session: Session, game_id: int, player_id: int) -> int:
    stmt = select(func.count(GameGuess.id)).where(
        GameGuess.game_id == game_id,
        GameGuess.player_id == player_id,
        GameGuess.correct.is_(False),
    )
    return int(session.scalar(stmt) or 0)


def win_facts(session: Session, game: Game, winner_id: int, how: WinMethod) -> dict[str, Any]:
    now = datetime.now(UTC)
    ended = game.ended_at or now
    if ended.tzinfo is None:
        ended = ended.replace(tzinfo=UTC)
    return {
        "stage": stage_number(game),
        "hard_mode": game.hard_mode,
        "how": how.value,
        "pot": bounty.pot_balance(session, game.id),
        "seconds": _seconds_live(game, now),
        "last_slot": _on_last_slot(session, game),
        "distinct_guessers": len(guesses.guessers(session, game.id)),
        "winner_wrong": _wrong_guesses(session, game.id, winner_id),
        "first_guess": how is WinMethod.GUESS and game.total_guess_count == 1,
        "ended_at": ended.isoformat(),
    }
