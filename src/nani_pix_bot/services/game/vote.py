"""The hard-mode vote (issue #252). When a HARD MODE game would end with no
correct guess but someone did guess, the round goes to VOTING instead, and
the group picks who (if anyone) was actually right. See MECHANICS.md's
"HARD MODE vote". Telegram-free; jobs/timers/vote.py posts and closes it.

Imports hard_mode.py at module level (for HARD_MODE_WIN_AWARD); hard_mode
reaches back here with a function-local import — the same one-way rule
state.py and hard_mode.py already follow."""

import enum
from collections.abc import Mapping
from datetime import timedelta

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_vote import GameVote
from nani_pix_bot.services import players
from nani_pix_bot.services.game import guesses, state
from nani_pix_bot.services.game.clock import deadline_after
from nani_pix_bot.services.game.hard_mode import HARD_MODE_WIN_AWARD
from nani_pix_bot.services.game.state import GuessOutcome

VOTE_DURATION = timedelta(minutes=15)
# A winner needs a unique plurality of at least this many votes.
VOTE_MIN_VOTES = 3


class VoteRefusal(enum.StrEnum):
    CLOSED = "closed"
    SELF = "self"
    NOT_CANDIDATE = "not_candidate"


def end_hard_mode_without_winner(session: Session, game: Game, *, cause: str) -> GuessOutcome:
    """Where every hard-mode path that runs out of turns or time lands,
    instead of state.force_unsolved: a vote if anyone guessed, else UNSOLVED."""
    candidates = guesses.guessers(session, game.id)
    if not candidates:
        state.force_unsolved(game, cause=f"{cause}; nobody guessed, so no vote")
        return GuessOutcome.UNSOLVED
    game.status = GameStatus.VOTING
    closes_at = deadline_after(session, VOTE_DURATION)
    game.vote_deadline_at = closes_at
    logger.info(
        "no correct guess ({cause}) — vote opened with {count} candidate(s), closes at {closes}",
        cause=cause,
        count=len(candidates),
        closes=closes_at.isoformat(timespec="minutes"),
        game_id=game.id,
    )
    return GuessOutcome.VOTE_OPENED


def decide_winner(counts: Mapping[int, int], *, min_votes: int = VOTE_MIN_VOTES) -> int | None:
    """The unique top candidate, if they have at least `min_votes`."""
    if not counts:
        return None
    ranked = sorted(counts.items(), key=lambda item: -item[1])
    top_id, top = ranked[0]
    if top < min_votes or (len(ranked) > 1 and ranked[1][1] == top):
        return None
    return top_id


def vote_counts(session: Session, game_id: int) -> dict[int, int]:
    counts: dict[int, int] = {}
    stmt = select(GameVote.candidate_id).where(GameVote.game_id == game_id)
    for candidate_id in session.scalars(stmt):
        counts[candidate_id] = counts.get(candidate_id, 0) + 1
    return counts


def _refusal(
    session: Session, game: Game, *, voter_id: int, candidate_id: int
) -> VoteRefusal | None:
    if game.status != GameStatus.VOTING:
        return VoteRefusal.CLOSED
    if voter_id == candidate_id:
        return VoteRefusal.SELF
    if candidate_id not in guesses.guessers(session, game.id):
        return VoteRefusal.NOT_CANDIDATE
    return None


def cast_vote(
    session: Session, game: Game, *, voter_id: int, candidate_id: int
) -> VoteRefusal | None:
    refusal = _refusal(session, game, voter_id=voter_id, candidate_id=candidate_id)
    if refusal is not None:
        logger.warning(
            "vote for {candidate} refused: {reason}",
            candidate=players.describe_player_id(session, candidate_id),
            candidate_id=candidate_id,
            reason=refusal.value,
            game_id=game.id,
        )
        return refusal
    stmt = select(GameVote).where(GameVote.game_id == game.id, GameVote.voter_id == voter_id)
    row = session.scalars(stmt).first()
    if row is None:
        session.add(GameVote(game_id=game.id, voter_id=voter_id, candidate_id=candidate_id))
    else:
        row.candidate_id = candidate_id
    session.flush()
    logger.info(
        "voted for {candidate}",
        candidate=players.describe_player_id(session, candidate_id),
        candidate_id=candidate_id,
        game_id=game.id,
    )
    return None


def close_vote(session: Session, game: Game, *, forced_winner_id: int | None = None) -> int | None:
    """End the vote: the tallied (or an admin's forced) winner gets a normal
    hard-mode win, else the game ends UNSOLVED. Returns the winner id."""
    counts = vote_counts(session, game.id)
    winner_id = forced_winner_id if forced_winner_id is not None else decide_winner(counts)
    if winner_id is None:
        state.force_unsolved(game, cause=f"vote closed with no winner (votes {counts})")
        return None
    how = (
        "named by an admin"
        if forced_winner_id is not None
        else f"won with {counts[winner_id]} vote(s)"
    )
    logger.info(
        "vote closed — {winner} {how}",
        winner=players.describe_player_id(session, winner_id),
        winner_id=winner_id,
        how=how,
        game_id=game.id,
    )
    state._win(session, game, winner_id=winner_id, award=HARD_MODE_WIN_AWARD)
    return winner_id
