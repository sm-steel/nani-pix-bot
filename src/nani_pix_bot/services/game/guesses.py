"""The per-game guess log (issue #251): every /guess as a `game_guesses`
row. Read by the hard-mode vote (who can be voted for, and what they
guessed) and by the clue shop (a partial reveal locks the title-shape clue)."""

from dataclasses import dataclass

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import EventType
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GUESS_TEXT_LENGTH, GameGuess
from nani_pix_bot.services import events


@dataclass(frozen=True)
class GuessRecord:
    player_id: int
    text: str
    stage: int
    correct: bool
    partial_reveal: str | None = None
    score: float | None = None


def _reveal_is_new(session: Session, game_id: int, reveal: str | None) -> bool:
    if reveal is None:
        return False
    stmt = select(GameGuess.id).where(
        GameGuess.game_id == game_id, GameGuess.partial_reveal == reveal
    )
    return session.scalars(stmt.limit(1)).first() is None


def log_guess(session: Session, game: Game, record: GuessRecord) -> GameGuess:
    reveal_new = _reveal_is_new(session, game.id, record.partial_reveal)
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
    events.emit(
        session,
        EventType.GUESS,
        events.Involved(actor_id=record.player_id, game_id=game.id),
        stage=record.stage,
        hard_mode=game.hard_mode,
        correct=record.correct,
        # record_guess counts the guess before logging it.
        first_of_game=game.total_guess_count == 1,
        reveal_new=reveal_new,
        score=record.score,
    )
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


def guess_texts(session: Session, game_id: int) -> dict[int, list[str]]:
    """Each guesser's guesses, oldest first; the dict is in first-guess order."""
    texts: dict[int, list[str]] = {}
    rows = session.execute(
        select(GameGuess.player_id, GameGuess.text)
        .where(GameGuess.game_id == game_id)
        .order_by(GameGuess.id)
    )
    for player_id, text in rows:
        texts.setdefault(player_id, []).append(text)
    return texts


def guessers(session: Session, game_id: int) -> list[int]:
    """Everyone who guessed in the game, in first-guess order — the people a
    hard-mode vote can name."""
    return list(guess_texts(session, game_id))
