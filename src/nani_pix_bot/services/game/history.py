"""Finished games for /history (DM): the list, newest first, optionally
narrowed to one player's games, and one game's full record. Only WON and
UNSOLVED games — never a running one, so nothing here can spoil a round.

The game row says who hosted and who won; how it was won, the pot, the
time it took and an unsolved game's cause come from its event_log entry,
and the guess log from game_guesses. Games older than those records
simply lack those parts (event_log arrived with achievements, guesses
with issue #251)."""

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import EventType, GameStatus
from nani_pix_bot.models.event_log import EventLog
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.services.achievements import periods
from nani_pix_bot.services.events import LoggedEvent, to_event

FINISHED = (GameStatus.WON, GameStatus.UNSOLVED)


class UnsolvedReason(StrEnum):
    """Why a game ended unsolved, read back from the cause force_unsolved()
    logged — that text is written for operators, not players."""

    TIMEOUT = "timeout"
    EXHAUSTED = "exhausted"
    NO_VOTE = "no_vote"
    VOTE_FAILED = "vote_failed"
    OTHER = "other"


def unsolved_reason(cause: str) -> UnsolvedReason:
    if "nobody guessed" in cause:  # HARD MODE: no vote held (checked first: its prefix varies)
        return UnsolvedReason.NO_VOTE
    if cause.startswith("2-day timeout"):
        return UnsolvedReason.TIMEOUT
    if cause.startswith("final stage exhausted"):
        return UnsolvedReason.EXHAUSTED
    if cause.startswith("vote closed"):
        return UnsolvedReason.VOTE_FAILED
    return UnsolvedReason.OTHER


def _finished(player_id: int | None):
    stmt = select(Game).where(Game.status.in_(FINISHED))
    if player_id is None:
        return stmt
    guessed = exists().where(GameGuess.game_id == Game.id, GameGuess.player_id == player_id)
    return stmt.where(or_(Game.starter_id == player_id, Game.winner_id == player_id, guessed))


def finished_games(
    session: Session, *, player_id: int | None = None, limit: int, offset: int = 0
) -> list[Game]:
    """Newest first: by when the game ended, or when it started for a game
    that ended before ended_at was recorded (issue #239)."""
    when = func.coalesce(Game.ended_at, Game.created_at)
    stmt = _finished(player_id).order_by(when.desc(), Game.id.desc()).limit(limit).offset(offset)
    return list(session.scalars(stmt))


def count_finished(session: Session, *, player_id: int | None = None) -> int:
    stmt = select(func.count()).select_from(_finished(player_id).subquery())
    return int(session.scalar(stmt) or 0)


@dataclass(frozen=True)
class GameDetail:
    game: Game
    guesses: list[GameGuess]
    how: str | None = None  # WinMethod value
    pot: int | None = None
    seconds: float | None = None
    points: int | None = None  # 🌟 the winner got
    unsolved: UnsolvedReason | None = None  # None: not recorded, or the game was won


def _latest_event(session: Session, event_type: EventType, game_id: int) -> LoggedEvent | None:
    stmt = (
        select(EventLog)
        .where(EventLog.event_type == event_type, EventLog.game_id == game_id)
        .order_by(EventLog.id.desc())
        .limit(1)
    )
    row = session.scalars(stmt).first()
    return to_event(row) if row is not None else None


def game_detail(session: Session, game_id: int) -> GameDetail | None:
    """None for a game that doesn't exist (a /stop deletes it) or isn't over."""
    game = session.get(Game, game_id)
    if game is None or game.status not in FINISHED:
        return None
    guesses_stmt = (
        select(GameGuess)
        .where(GameGuess.game_id == game_id)
        .order_by(GameGuess.created_at, GameGuess.id)
    )
    guesses = list(session.scalars(guesses_stmt))
    if game.status is GameStatus.UNSOLVED:
        unsolved = _latest_event(session, EventType.GAME_UNSOLVED, game_id)
        cause = unsolved.data.get("cause") if unsolved is not None else None
        reason = unsolved_reason(cause) if isinstance(cause, str) else None
        return GameDetail(game, guesses, unsolved=reason)
    won = _latest_event(session, EventType.GAME_WON, game_id)
    if won is None:
        return GameDetail(game, guesses)
    return GameDetail(
        game,
        guesses,
        how=won.data.get("how"),
        pot=won.data.get("pot"),
        seconds=won.data.get("seconds"),
        points=periods.win_points(won),
    )
