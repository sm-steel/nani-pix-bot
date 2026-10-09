"""Finished games for /history (DM): the list, newest first, optionally
narrowed to the games one player played in, hosted or won, and one game's
full record. Only WON and
UNSOLVED games — never a running one, so nothing here can spoil a round.

The game row says who hosted and who won; how it was won, the pot, the
time it took, how many players guessed and an unsolved game's cause come
from its event_log entry, the stage timeline from its stage_advanced
events, and the guess log from game_guesses. Clues come from
clue_purchases with the price of their currency_transfers charge (a
refunded clue's row is deleted, so it doesn't appear), the bounty from
its unrefunded pot contributions and HARD MODE votes from game_votes. Games older than those records
simply lack those parts (event_log arrived with achievements, guesses
with issue #251)."""

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.clue_purchase import CluePurchase
from nani_pix_bot.models.currency_transfer import CurrencyTransfer
from nani_pix_bot.models.enums import ClueKind, EventType, GameStatus
from nani_pix_bot.models.event_log import EventLog
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.models.game_vote import GameVote
from nani_pix_bot.services.achievements import periods
from nani_pix_bot.services.economy import bounty
from nani_pix_bot.services.events import LoggedEvent, as_utc, to_event

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


class Involvement(StrEnum):
    """Which finished games a list shows."""

    ALL = "a"
    PLAYED = "m"  # hosted, won or guessed in
    HOSTED = "h"
    WON = "w"


class StageReason(StrEnum):
    """Why a stage advanced, read back from the reason advance_stage() logged."""

    EXHAUSTED = "exhausted"
    SHARPENED = "sharpened"
    INACTIVITY = "inactivity"
    OTHER = "other"


_STAGE_REASONS = {
    "wrong-guess limit reached": StageReason.EXHAUSTED,
    "sharpened": StageReason.SHARPENED,
    "no guesses for 6h": StageReason.INACTIVITY,
}


def stage_reason(reason: str) -> StageReason:
    return _STAGE_REASONS.get(reason, StageReason.OTHER)


@dataclass(frozen=True)
class Scope:
    """Which games a list shows: every filter but ALL is about `player_id`."""

    filt: Involvement = Involvement.ALL
    player_id: int | None = None

    def __post_init__(self) -> None:
        if self.filt is not Involvement.ALL and self.player_id is None:
            msg = f"the {self.filt.name.lower()} filter needs a player"
            raise ValueError(msg)


EVERY_GAME = Scope()


def _finished(scope: Scope):
    stmt = select(Game).where(Game.status.in_(FINISHED))
    player_id = scope.player_id
    if scope.filt is Involvement.ALL or player_id is None:
        return stmt
    guessed = exists().where(GameGuess.game_id == Game.id, GameGuess.player_id == player_id)
    hosted, won = Game.starter_id == player_id, Game.winner_id == player_id
    involved = {
        Involvement.PLAYED: or_(hosted, won, guessed),
        Involvement.HOSTED: hosted,
        Involvement.WON: won,
    }
    return stmt.where(involved[scope.filt])


def finished_games(
    session: Session, scope: Scope = EVERY_GAME, *, limit: int, offset: int = 0
) -> list[Game]:
    """Newest first: by when the game ended, or when it started for a game
    that ended before ended_at was recorded (issue #239)."""
    when = func.coalesce(Game.ended_at, Game.created_at)
    stmt = _finished(scope).order_by(when.desc(), Game.id.desc())
    return list(session.scalars(stmt.limit(limit).offset(offset)))


def count_finished(session: Session, scope: Scope = EVERY_GAME) -> int:
    stmt = select(func.count()).select_from(_finished(scope).subquery())
    return int(session.scalar(stmt) or 0)


@dataclass(frozen=True)
class ClueRow:
    at: datetime
    player_id: int
    kind: ClueKind
    price: int
    shared_at: datetime | None


@dataclass(frozen=True)
class StageStep:
    at: datetime
    from_stage: int
    to_stage: int
    reason: StageReason


@dataclass(frozen=True)
class GameDetail:
    game: Game
    guesses: list[GameGuess]
    how: str | None = None  # WinMethod value
    pot: int | None = None
    seconds: float | None = None
    points: int | None = None  # 🌟 the winner got
    unsolved: UnsolvedReason | None = None  # None: not recorded, or the game was won
    clues: list[ClueRow] = field(default_factory=list)
    bounty: list[tuple[int, int]] = field(default_factory=list)  # (player_id, total)
    stages: list[StageStep] = field(default_factory=list)
    distinct_guessers: int | None = None
    votes: list[tuple[int, int]] = field(default_factory=list)  # (voter_id, candidate_id)


def _latest_event(session: Session, event_type: EventType, game_id: int) -> LoggedEvent | None:
    stmt = (
        select(EventLog)
        .where(EventLog.event_type == event_type, EventLog.game_id == game_id)
        .order_by(EventLog.id.desc())
        .limit(1)
    )
    row = session.scalars(stmt).first()
    return to_event(row) if row is not None else None


def _clue_row(purchase: CluePurchase, price: int) -> ClueRow:
    shared = purchase.shared_at
    return ClueRow(
        at=as_utc(purchase.created_at),
        player_id=purchase.player_id,
        kind=ClueKind(purchase.kind),
        price=price,
        shared_at=as_utc(shared) if shared is not None else None,
    )


def _clues(session: Session, game_id: int) -> list[ClueRow]:
    stmt = (
        select(CluePurchase, CurrencyTransfer.amount)
        .join(CurrencyTransfer, CurrencyTransfer.id == CluePurchase.transfer_id)
        .where(CluePurchase.game_id == game_id)
        .order_by(CluePurchase.created_at, CluePurchase.id)
    )
    return [_clue_row(purchase, price) for purchase, price in session.execute(stmt).tuples()]


def _bounty(session: Session, game_id: int) -> list[tuple[int, int]]:
    """Each contributor's total, in the order they first put in."""
    totals: dict[int, int] = {}
    for contribution in bounty.contributions(session, game_id):
        player_id = contribution.from_player_id
        if player_id is not None:
            totals[player_id] = totals.get(player_id, 0) + contribution.amount
    return list(totals.items())


def _stage_step(event: LoggedEvent) -> StageStep:
    reason = event.data.get("reason")
    return StageStep(
        at=event.occurred_at,
        from_stage=int(event.data["from_stage"]),
        to_stage=int(event.data["to_stage"]),
        reason=stage_reason(reason) if isinstance(reason, str) else StageReason.OTHER,
    )


def _stages(session: Session, game_id: int) -> list[StageStep]:
    stmt = (
        select(EventLog)
        .where(EventLog.event_type == EventType.STAGE_ADVANCED, EventLog.game_id == game_id)
        .order_by(EventLog.id)
    )
    return [_stage_step(to_event(row)) for row in session.scalars(stmt)]


def _votes(session: Session, game_id: int) -> list[tuple[int, int]]:
    stmt = select(GameVote).where(GameVote.game_id == game_id).order_by(GameVote.id)
    return [(vote.voter_id, vote.candidate_id) for vote in session.scalars(stmt)]


def _record(session: Session, game: Game) -> GameDetail:
    """Everything but the outcome."""
    guesses_stmt = (
        select(GameGuess)
        .where(GameGuess.game_id == game.id)
        .order_by(GameGuess.created_at, GameGuess.id)
    )
    return GameDetail(
        game,
        list(session.scalars(guesses_stmt)),
        clues=_clues(session, game.id),
        bounty=_bounty(session, game.id),
        stages=_stages(session, game.id),
        votes=_votes(session, game.id),
    )


def _guessers(event: LoggedEvent) -> int | None:
    guessers = event.data.get("distinct_guessers")
    return guessers if isinstance(guessers, int) else None


def _unsolved(session: Session, detail: GameDetail) -> GameDetail:
    unsolved = _latest_event(session, EventType.GAME_UNSOLVED, detail.game.id)
    if unsolved is None:
        return detail
    cause = unsolved.data.get("cause")
    reason = unsolved_reason(cause) if isinstance(cause, str) else None
    return replace(detail, unsolved=reason, distinct_guessers=_guessers(unsolved))


def _won(session: Session, detail: GameDetail) -> GameDetail:
    won = _latest_event(session, EventType.GAME_WON, detail.game.id)
    if won is None:
        return detail
    return replace(
        detail,
        how=won.data.get("how"),
        pot=won.data.get("pot"),
        seconds=won.data.get("seconds"),
        points=periods.win_points(won),
        distinct_guessers=_guessers(won),
    )


def game_detail(session: Session, game_id: int) -> GameDetail | None:
    """None for a game that doesn't exist (a /stop deletes it) or isn't over."""
    game = session.get(Game, game_id)
    if game is None or game.status not in FINISHED:
        return None
    detail = _record(session, game)
    if game.status is GameStatus.UNSOLVED:
        return _unsolved(session, detail)
    return _won(session, detail)
