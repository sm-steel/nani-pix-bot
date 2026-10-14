from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.models.pass_vote import PassVote
from nani_pix_bot.models.player import Player
from nani_pix_bot.services.game import pass_vote

NOW = datetime(2026, 10, 14, 12, tzinfo=UTC)
HOST, BOT = 1, 999


def _naive(at: datetime) -> datetime:
    return at.astimezone(UTC).replace(tzinfo=None)


@pytest.fixture
def game(session: Session) -> Game:
    session.add_all(Player(telegram_user_id=i) for i in (HOST, BOT, *range(10, 30)))
    session.flush()
    row = Game(
        starter_id=HOST,
        status=GameStatus.ACTIVE,
        current_stage=PixelStage.STAGE_1,
        title_english="Frieren",
        activated_at=_naive(NOW - timedelta(hours=1)),
    )
    session.add(row)
    session.flush()
    return row


def _guessed(session: Session, game: Game, player_id: int, at: datetime) -> None:
    session.add(
        GameGuess(game_id=game.id, player_id=player_id, text="x", stage=1, created_at=_naive(at))
    )
    session.flush()


def _toggle(session: Session, game: Game, player_id: int) -> pass_vote.PassTally:
    active = pass_vote.recent_active_count(session, NOW, exclude={HOST, BOT})
    return pass_vote.toggle(session, game, player_id, active=active)


@pytest.mark.parametrize(("active", "needed"), [(0, 3), (3, 3), (6, 3), (7, 4), (8, 4), (12, 6)])
def test_threshold_is_half_the_active_players_but_never_below_three(active, needed) -> None:
    assert pass_vote.threshold(active) == needed


def test_active_counts_recent_guessers_and_hosts_once_each(session, game) -> None:
    _guessed(session, game, 10, NOW - timedelta(hours=23, minutes=59))
    _guessed(session, game, 10, NOW - timedelta(hours=1))  # same player again
    _guessed(session, game, 11, NOW - timedelta(hours=24, seconds=1))  # too old
    host_of_other = Game(
        starter_id=12,
        status=GameStatus.UNSOLVED,
        title_english="X",
        activated_at=_naive(NOW - timedelta(hours=2)),
    )
    session.add(host_of_other)
    session.flush()
    assert pass_vote.recent_active_count(session, NOW, exclude={HOST, BOT}) == 2  # 10 and 12


def test_active_leaves_out_the_excluded(session, game) -> None:
    _guessed(session, game, 10, NOW)
    # HOST hosted `game` within the window; BOT is excluded too.
    assert pass_vote.recent_active_count(session, NOW, exclude={HOST, BOT}) == 1
    assert pass_vote.recent_active_count(session, NOW, exclude=set()) == 2


def test_a_vote_then_the_same_command_again_withdraws_it(session, game) -> None:
    first = _toggle(session, game, 10)
    assert (first.voted, first.votes, first.needed) == (True, 1, 3)
    second = _toggle(session, game, 10)
    assert (second.voted, second.votes) == (False, 0)


def test_the_host_cannot_vote(session, game) -> None:
    with pytest.raises(pass_vote.PassRefusedError) as refused:
        _toggle(session, game, HOST)
    assert refused.value.refusal is pass_vote.PassRefusal.HOST
    assert session.scalars(select(PassVote)).all() == []


def test_three_votes_reach_the_minimum(session, game) -> None:
    for player in (10, 11):
        assert not _toggle(session, game, player).reached
    assert _toggle(session, game, 12).reached


def test_votes_from_an_earlier_stage_do_not_count(session, game) -> None:
    _toggle(session, game, 10)
    _toggle(session, game, 11)
    game.current_stage = PixelStage.STAGE_2
    session.flush()
    again = _toggle(session, game, 10)
    assert (again.voted, again.votes) == (True, 1)  # a new vote, not a withdrawal


def test_more_active_players_raise_the_bar(session, game) -> None:
    for player in range(10, 18):  # 8 recent guessers
        _guessed(session, game, player, NOW - timedelta(hours=1))
    tally = _toggle(session, game, 10)
    assert (tally.active, tally.needed) == (8, 4)
