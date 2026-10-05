"""The per-game guess log (issue #251): every /guess as a `game_guesses`
row. Read by the hard-mode vote (who can be voted for, and what they
guessed) and by the clue shop (a partial reveal locks the title-shape clue)."""

from dataclasses import dataclass

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GUESS_TEXT_LENGTH, GameGuess


@dataclass(frozen=True)
class GuessRecord:
    player_id: int
    text: str
    stage: int
    correct: bool
    partial_reveal: str | None = None


def log_guess(session: Session, game: Game, record: GuessRecord) -> GameGuess:
    row = GameGuess(
        game_id=game.id,
        player_id=record.player_id,
        text=record.text[:GUESS_TEXT_LENGTH],
        stage=record.stage,
        correct=record.correct,
        partial_reveal=record.partial_reveal,
    )
    session.add(row)
    session.flush()
    # DEBUG: record_guess already logs the guess and its verdict at INFO.
    logger.debug("guess logged (row {row_id})", row_id=row.id, game_id=game.id)
    return row


def latest(session: Session, game_id: int) -> GameGuess | None:
    """The game's most recent guess row, if any."""
    return session.scalars(
        select(GameGuess).where(GameGuess.game_id == game_id).order_by(GameGuess.id.desc())
    ).first()


def has_partial_reveal(session: Session, game_id: int) -> bool:
    """Whether any guess in the game already revealed part of the title
    (issue #250) — which takes the title-shape clue off the shelf."""
    return (
        session.scalars(
            select(GameGuess.id)
            .where(GameGuess.game_id == game_id, GameGuess.partial_reveal.is_not(None))
            .limit(1)
        ).first()
        is not None
    )
