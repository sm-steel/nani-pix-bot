from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models import Player
from nani_pix_bot.models.enums import EventType, GameStatus
from nani_pix_bot.models.event_log import EventLog
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services.game import guesses, vote

BOT, A, B, C, D = 100, 1, 2, 3, 4


def _hard_game(session: Session, *, guessers: tuple[int, ...] = (A, B)) -> Game:
    session.add_all([Player(telegram_user_id=n) for n in (BOT, A, B, C, D)])
    session.flush()
    game = Game(
        starter_id=BOT, status=GameStatus.ACTIVE, hard_mode=True, hard_mode_turn=2, title_romaji="X"
    )
    session.add(game)
    session.flush()
    for player in guessers:
        guesses.log_guess(
            session,
            game,
            guesses.GuessRecord(player_id=player, text=f"guess {player}", stage=2, correct=False),
        )
    return game


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ({}, None),
        ({A: 2}, None),
        ({A: 3}, A),
        ({A: 3, B: 3}, None),
        ({A: 4, B: 3}, A),
        ({A: 1, B: 5}, B),
        # Nobody (None) competes like a candidate, but its lead is no winner.
        ({vote.NOBODY: 3, A: 1}, None),
        ({vote.NOBODY: 3, A: 3}, None),
        ({A: 3, vote.NOBODY: 2}, A),
    ],
)
def test_decide_winner(counts: dict[int | None, int], expected: int | None) -> None:
    assert vote.decide_winner(counts, min_votes=3) == expected


@pytest.mark.parametrize(
    ("counts", "needed", "expected"),
    [
        ({}, 3, False),
        ({A: 3}, 3, True),
        ({A: 3, B: 3}, 3, False),
        ({vote.NOBODY: 3}, 3, True),
        ({vote.NOBODY: 3, A: 3}, 3, False),
        ({A: 3}, 4, False),
        ({A: 4, B: 3}, 4, True),
    ],
)
def test_decided_early(counts: dict[int | None, int], needed: int, expected: bool) -> None:
    """A unique lead of `needed` votes closes the vote now (issue #458)."""
    assert vote.decided_early(counts, needed=needed) is expected


def test_early_close_bar_is_the_pass_bar_without_the_host(session: Session) -> None:
    """max(3, ⌈active / 2⌉) of the last day's active players — /pass's
    bar — minus this game's host. A..D guessed: 4 active, so 3."""
    game = _hard_game(session, guessers=(A, B, C, D))
    vote.end_hard_mode_without_winner(session, game, cause="t")
    assert vote.early_close_needed(session, game, datetime.now(UTC)) == 3

    many = [Player(telegram_user_id=n) for n in range(10, 16)]
    session.add_all(many)
    session.flush()
    for player in many:
        record = guesses.GuessRecord(
            player_id=player.telegram_user_id, text="x", stage=2, correct=False
        )
        guesses.log_guess(session, game, record)
    assert vote.early_close_needed(session, game, datetime.now(UTC)) == 5  # 10 active


def test_should_close_early_once_a_lead_reaches_the_bar(session: Session) -> None:
    game = _hard_game(session, guessers=(A, B, C, D))
    vote.end_hard_mode_without_winner(session, game, cause="t")
    now = datetime.now(UTC)
    for voter in (B, C):
        vote.cast_vote(session, game, voter_id=voter, candidate_id=vote.NOBODY)
    assert not vote.should_close_early(session, game, now)
    vote.cast_vote(session, game, voter_id=D, candidate_id=vote.NOBODY)
    assert vote.should_close_early(session, game, now)


def test_nobody_vote_is_counted_and_never_refused_as_self(session: Session) -> None:
    game = _hard_game(session)
    vote.end_hard_mode_without_winner(session, game, cause="t")
    assert vote.cast_vote(session, game, voter_id=A, candidate_id=vote.NOBODY) is None
    assert vote.cast_vote(session, game, voter_id=C, candidate_id=vote.NOBODY) is None
    assert vote.vote_counts(session, game.id) == {vote.NOBODY: 2}


def test_close_with_nobody_in_the_lead_is_unsolved(session: Session) -> None:
    game = _hard_game(session)
    vote.end_hard_mode_without_winner(session, game, cause="t")
    for voter in (B, C, D):
        vote.cast_vote(session, game, voter_id=voter, candidate_id=vote.NOBODY)
    vote.cast_vote(session, game, voter_id=A, candidate_id=B)

    assert vote.close_vote(session, game) is None
    assert game.status is GameStatus.UNSOLVED
    stmt = select(EventLog).where(EventLog.event_type == EventType.VOTE_COUNTED)
    rows = list(session.scalars(stmt))
    assert sorted((r.actor_id, r.subject_id or 0, r.data["won"]) for r in rows) == [
        (A, B, False),
        (B, 0, False),
        (C, 0, False),
        (D, 0, False),
    ]


def test_no_guessers_ends_unsolved(session: Session) -> None:
    game = _hard_game(session, guessers=())
    outcome = vote.end_hard_mode_without_winner(session, game, cause="t")
    assert outcome is game_service.GuessOutcome.UNSOLVED
    assert game.status is GameStatus.UNSOLVED


def test_guessers_open_a_vote(session: Session) -> None:
    game = _hard_game(session)
    before = datetime.now(UTC).replace(tzinfo=None)
    outcome = vote.end_hard_mode_without_winner(session, game, cause="t")
    assert outcome is game_service.GuessOutcome.VOTE_OPENED
    assert game.status is GameStatus.VOTING
    assert game.ended_at is None
    assert game.vote_deadline_at is not None
    assert game.vote_deadline_at.replace(tzinfo=None) >= before


def test_cast_vote_rules(session: Session) -> None:
    game = _hard_game(session)
    vote.end_hard_mode_without_winner(session, game, cause="t")

    assert vote.cast_vote(session, game, voter_id=A, candidate_id=A) is vote.VoteRefusal.SELF
    assert (
        vote.cast_vote(session, game, voter_id=C, candidate_id=D) is vote.VoteRefusal.NOT_CANDIDATE
    )
    assert vote.cast_vote(session, game, voter_id=C, candidate_id=A) is None
    assert vote.cast_vote(session, game, voter_id=C, candidate_id=B) is None  # changed their mind
    assert vote.vote_counts(session, game.id) == {B: 1}


def test_cast_vote_after_close_is_refused(session: Session) -> None:
    game = _hard_game(session)
    vote.end_hard_mode_without_winner(session, game, cause="t")
    vote.close_vote(session, game)
    assert vote.cast_vote(session, game, voter_id=C, candidate_id=A) is vote.VoteRefusal.CLOSED


def test_close_with_a_winner_awards_the_hard_mode_win(session: Session) -> None:
    game = _hard_game(session)
    vote.end_hard_mode_without_winner(session, game, cause="t")
    for voter in (B, C, D):
        vote.cast_vote(session, game, voter_id=voter, candidate_id=A)

    assert vote.close_vote(session, game) == A
    assert game.status is GameStatus.WON
    assert game.winner_id == A
    winner = session.get(Player, A)
    assert winner is not None
    assert winner.wins == 2
    turn_state = game_service.get_turn_state(session)
    assert turn_state is not None
    assert turn_state.next_starter_id == A


def test_close_without_enough_votes_is_unsolved(session: Session) -> None:
    game = _hard_game(session)
    vote.end_hard_mode_without_winner(session, game, cause="t")
    vote.cast_vote(session, game, voter_id=C, candidate_id=A)

    assert vote.close_vote(session, game) is None
    assert game.status is GameStatus.UNSOLVED
    assert game.ended_at is not None


def test_forced_winner_skips_the_tally(session: Session) -> None:
    game = _hard_game(session)
    vote.end_hard_mode_without_winner(session, game, cause="t")
    assert vote.close_vote(session, game, forced_winner_id=B) == B
    assert game.winner_id == B


def test_closing_a_vote_logs_one_event_per_ballot(session: Session) -> None:
    game = _hard_game(session)
    vote.end_hard_mode_without_winner(session, game, cause="t")
    for voter in (B, C, D):
        vote.cast_vote(session, game, voter_id=voter, candidate_id=A)
    vote.cast_vote(session, game, voter_id=A, candidate_id=B)

    vote.close_vote(session, game)

    stmt = select(EventLog).where(EventLog.event_type == EventType.VOTE_COUNTED)
    rows = list(session.scalars(stmt.order_by(EventLog.id)))
    assert sorted((r.actor_id, r.subject_id, r.data["won"]) for r in rows) == [
        (A, B, False),
        (B, A, True),
        (C, A, True),
        (D, A, True),
    ]


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ({vote.NOBODY: 3}, True),
        ({vote.NOBODY: 2}, False),  # too few to call it the group's pick
        ({vote.NOBODY: 3, A: 3}, False),
        ({A: 3, vote.NOBODY: 1}, False),
    ],
)
def test_nobody_decided(counts: dict[int | None, int], expected: bool) -> None:
    assert vote.nobody_decided(counts) is expected


def test_a_changed_vote_that_breaks_a_tie_closes_early(session: Session) -> None:
    """3-3 between A and B; one of B's voters moves to Nobody, so A leads
    alone at the bar — whoever taps, the tally decides."""
    game = _hard_game(session, guessers=(A, B))
    extra = [Player(telegram_user_id=n) for n in range(10, 15)]
    session.add_all(extra)
    session.flush()
    vote.end_hard_mode_without_winner(session, game, cause="t")
    now = datetime.now(UTC)
    for voter in (B, 10, 11):
        vote.cast_vote(session, game, voter_id=voter, candidate_id=A)
    for voter in (A, 12, 13):
        vote.cast_vote(session, game, voter_id=voter, candidate_id=B)
    assert not vote.should_close_early(session, game, now)

    vote.cast_vote(session, game, voter_id=13, candidate_id=vote.NOBODY)

    assert vote.should_close_early(session, game, now)
    assert vote.close_vote(session, game) == A
