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
    ],
)
def test_decide_winner(counts: dict[int, int], expected: int | None) -> None:
    assert vote.decide_winner(counts, min_votes=3) == expected


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
