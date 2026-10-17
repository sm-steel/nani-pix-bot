"""The hard-mode vote (issue #252). When a HARD MODE game would end with no
correct guess but someone did guess, the round goes to VOTING instead, and
the group picks who (if anyone) was actually right. See MECHANICS.md's
"HARD MODE vote". Telegram-free; jobs/timers/vote.py posts and closes it.

Imports hard_mode.py at module level (for HARD_MODE_WIN_AWARD); hard_mode
reaches back here with a function-local import — the same one-way rule
state.py and hard_mode.py already follow."""

import enum
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import EventType, GameStatus, WinMethod
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_vote import GameVote
from nani_pix_bot.services import events, players
from nani_pix_bot.services.game import guesses, pass_vote, state
from nani_pix_bot.services.game.clock import deadline_after
from nani_pix_bot.services.game.hard_mode import HARD_MODE_WIN_AWARD
from nani_pix_bot.services.game.state import GuessOutcome

VOTE_DURATION = timedelta(minutes=15)
# A winner needs a unique plurality of at least this many votes.
VOTE_MIN_VOTES = 3
# The ballot's "Nobody" option — no guess was right (issue #458). It is a
# vote's candidate like any guesser, stored as a NULL candidate_id.
NOBODY: None = None


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


@dataclass(frozen=True)
class _Leader:
    candidate_id: int | None  # NOBODY included
    votes: int


def _unique_leader(counts: Mapping[int | None, int]) -> _Leader | None:
    """The option with strictly the most votes, Nobody included; None on a
    tie for the top or no votes at all."""
    if not counts:
        return None
    ranked = sorted(counts.items(), key=lambda item: -item[1])
    top_id, top = ranked[0]
    if len(ranked) > 1 and ranked[1][1] == top:
        return None
    return _Leader(top_id, top)


def decide_winner(
    counts: Mapping[int | None, int], *, min_votes: int = VOTE_MIN_VOTES
) -> int | None:
    """The unique top candidate, if they have at least `min_votes`. Nobody
    in the lead is no winner."""
    leader = _unique_leader(counts)
    if leader is None or leader.votes < min_votes:
        return None
    return leader.candidate_id


def decided_early(counts: Mapping[int | None, int], *, needed: int) -> bool:
    """Whether a unique lead — a guesser's or Nobody's — has `needed` votes,
    which closes the vote before its deadline."""
    leader = _unique_leader(counts)
    return leader is not None and leader.votes >= needed


def early_close_needed(session: Session, game: Game, now: datetime) -> int:
    """The early-close bar: /pass's, max(3, ⌈active / 2⌉) of the players
    active in the last day, minus this game's host."""
    active = pass_vote.recent_active_count(session, now, exclude={game.starter_id})
    return pass_vote.threshold(active)


def should_close_early(session: Session, game: Game, now: datetime) -> bool:
    counts = vote_counts(session, game.id)
    needed = early_close_needed(session, game, now)
    leader = _unique_leader(counts)
    if leader is None or not decided_early(counts, needed=needed):
        return False
    logger.info(
        "{leader} leads with {votes} vote(s), at the early-close bar of {needed} — closing",
        leader=_describe(session, leader.candidate_id),
        votes=leader.votes,
        needed=needed,
        game_id=game.id,
    )
    return True


def nobody_decided(counts: Mapping[int | None, int]) -> bool:
    """Whether the group actively picked Nobody: its unique lead with at
    least VOTE_MIN_VOTES, not just a vote that found no winner."""
    leader = _unique_leader(counts)
    return leader is not None and leader.candidate_id is NOBODY and leader.votes >= VOTE_MIN_VOTES


def vote_counts(session: Session, game_id: int) -> dict[int | None, int]:
    counts: dict[int | None, int] = {}
    stmt = select(GameVote.candidate_id).where(GameVote.game_id == game_id)
    for candidate_id in session.scalars(stmt):
        counts[candidate_id] = counts.get(candidate_id, 0) + 1
    return counts


def _log_votes(session: Session, game: Game, winner_id: int | None) -> None:
    """One `vote_counted` event per ballot cast, flagged by whether its
    candidate won the vote."""
    for vote in session.scalars(select(GameVote).where(GameVote.game_id == game.id)):
        events.emit(
            session,
            EventType.VOTE_COUNTED,
            events.Involved(actor_id=vote.voter_id, subject_id=vote.candidate_id, game_id=game.id),
            won=winner_id is not None and vote.candidate_id == winner_id,
        )


def _describe(session: Session, candidate_id: int | None) -> str:
    if candidate_id is NOBODY:
        return "Nobody"
    return players.describe_player_id(session, candidate_id)


def _tally(session: Session, counts: Mapping[int | None, int]) -> str:
    """The counts for a log line, Nobody by name rather than as `None`."""
    return ", ".join(f"{_describe(session, cid)}: {n}" for cid, n in counts.items()) or "none"


def _refusal(
    session: Session, game: Game, *, voter_id: int, candidate_id: int | None
) -> VoteRefusal | None:
    if game.status != GameStatus.VOTING:
        return VoteRefusal.CLOSED
    if candidate_id is NOBODY:
        return None
    if voter_id == candidate_id:
        return VoteRefusal.SELF
    if candidate_id not in guesses.guessers(session, game.id):
        return VoteRefusal.NOT_CANDIDATE
    return None


def cast_vote(
    session: Session, game: Game, *, voter_id: int, candidate_id: int | None
) -> VoteRefusal | None:
    """`candidate_id` NOBODY is a vote that no guess was right."""
    refusal = _refusal(session, game, voter_id=voter_id, candidate_id=candidate_id)
    if refusal is not None:
        logger.warning(
            "vote for {candidate} refused: {reason}",
            candidate=_describe(session, candidate_id),
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
        candidate=_describe(session, candidate_id),
        candidate_id=candidate_id,
        game_id=game.id,
    )
    return None


def close_vote(session: Session, game: Game, *, forced_winner_id: int | None = None) -> int | None:
    """End the vote: the tallied (or an admin's forced) winner gets a normal
    hard-mode win, else the game ends UNSOLVED. Returns the winner id."""
    counts = vote_counts(session, game.id)
    winner_id = forced_winner_id if forced_winner_id is not None else decide_winner(counts)
    _log_votes(session, game, winner_id)
    if winner_id is None:
        state.force_unsolved(
            game, cause=f"vote closed with no winner (votes {_tally(session, counts)})"
        )
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
    method = WinMethod.SETWINNER if forced_winner_id is not None else WinMethod.VOTE
    terms = state.WinTerms(award=HARD_MODE_WIN_AWARD, how=method)
    state._win(session, game, winner_id=winner_id, terms=terms)
    return winner_id
