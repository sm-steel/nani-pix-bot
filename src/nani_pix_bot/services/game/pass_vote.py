"""`/pass`: the group gives up on a screenshot nobody recognizes (#428). Pure game logic:
the threshold, who counts as active, and the vote toggle. The command handler ends the
game once the tally is reached."""

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from fractions import Fraction

from loguru import logger
from sqlalchemy import func, select, union
from sqlalchemy.orm import Session

from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.models.pass_vote import PassVote
from nani_pix_bot.services.game import win_facts

PASS_MIN_VOTES = 3
PASS_VOTE_SHARE = Fraction(1, 2)
PASS_ACTIVE_WINDOW = timedelta(hours=24)


class PassRefusal(StrEnum):
    HOST = "host"  # they know the answer


class PassRefusedError(Exception):
    def __init__(self, refusal: PassRefusal) -> None:
        super().__init__(refusal.value)
        self.refusal = refusal


@dataclass(frozen=True)
class PassTally:
    voted: bool  # False: this call withdrew the player's vote
    votes: int
    needed: int
    active: int

    @property
    def reached(self) -> bool:
        return self.votes >= self.needed


def threshold(active: int) -> int:
    """Votes needed: half the recently active players, rounded up, never below three."""
    return max(PASS_MIN_VOTES, math.ceil(active * PASS_VOTE_SHARE))


def recent_active_count(session: Session, now: datetime, *, exclude: set[int]) -> int:
    """Distinct players who guessed, or hosted a game that went live, in the window."""
    since = (now - PASS_ACTIVE_WINDOW).astimezone(UTC).replace(tzinfo=None)
    guessers = select(GameGuess.player_id).where(GameGuess.created_at >= since)
    hosts = select(Game.starter_id).where(Game.activated_at >= since)
    ids = set(session.execute(union(guessers, hosts)).scalars())
    return len(ids - exclude)


def toggle(session: Session, game: Game, player_id: int, *, active: int) -> PassTally:
    """Casts `player_id`'s vote for the current round, or withdraws it, and returns the
    tally against `active` players (recent_active_count). The host is refused."""
    if player_id == game.starter_id:
        raise PassRefusedError(PassRefusal.HOST)
    stage = win_facts.stage_number(game)
    mine = session.scalar(
        select(PassVote).where(
            PassVote.game_id == game.id, PassVote.player_id == player_id, PassVote.stage == stage
        )
    )
    if mine is None:
        session.add(PassVote(game_id=game.id, player_id=player_id, stage=stage))
    else:
        session.delete(mine)
    session.flush()
    votes = (
        session.scalar(
            select(func.count()).where(PassVote.game_id == game.id, PassVote.stage == stage)
        )
        or 0
    )
    tally = PassTally(voted=mine is None, votes=votes, needed=threshold(active), active=active)
    logger.debug(
        "pass tally {votes}/{needed} at round {stage} ({change})",
        votes=votes,
        needed=tally.needed,
        stage=stage,
        change="cast" if tally.voted else "withdrawn",
        game_id=game.id,
    )
    return tally
