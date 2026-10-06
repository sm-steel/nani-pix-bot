from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from nani_pix_bot.models import Player
from nani_pix_bot.models.enums import GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services.game import turns
from nani_pix_bot.services.game.refinish import RefinishRefusal, refinish, refinish_refusal

BOT, STARTER, WINNER = 100, 1, 2
ENDED = datetime(2026, 10, 5, 21, 11, tzinfo=UTC)


def _unsolved(session: Session, **fields) -> Game:
    session.add_all([Player(telegram_user_id=n) for n in (BOT, STARTER, WINNER)])
    session.flush()
    defaults = {
        "starter_id": BOT,
        "status": GameStatus.UNSOLVED,
        "hard_mode": True,
        "hard_mode_turn": 2,
        "ended_at": ENDED,
        "title_romaji": "X",
    }
    defaults.update(fields)
    game = Game(**defaults)
    session.add(game)
    session.flush()
    return game


def test_refusals(session: Session) -> None:
    game = _unsolved(session)
    assert refinish_refusal(None, winner_id=WINNER, bot_id=BOT) is RefinishRefusal.NOT_FOUND
    assert refinish_refusal(game, winner_id=BOT, bot_id=BOT) is RefinishRefusal.BOT
    game.status = GameStatus.WON
    assert refinish_refusal(game, winner_id=WINNER, bot_id=BOT) is RefinishRefusal.NOT_UNSOLVED
    game.status = GameStatus.VOTING
    assert refinish_refusal(game, winner_id=WINNER, bot_id=BOT) is None


def test_starter_cannot_be_named(session: Session) -> None:
    game = _unsolved(
        session,
        starter_id=STARTER,
        hard_mode=False,
        hard_mode_turn=None,
        current_stage=PixelStage.STAGE_5,
    )
    assert refinish_refusal(game, winner_id=STARTER, bot_id=BOT) is RefinishRefusal.STARTER


def test_refinish_marks_the_win_without_moving_the_turn_or_end_time(session: Session) -> None:
    game = _unsolved(session)
    turn = turns.get_or_create_turn_state(session)
    turn.next_starter_id = None

    refinish(session, game, winner_id=WINNER)

    assert game.status is GameStatus.WON
    assert game.winner_id == WINNER
    assert game.ended_at is not None
    assert game.ended_at.replace(tzinfo=UTC) == ENDED
    winner = session.get(Player, WINNER)
    assert winner is not None
    assert winner.wins == game_service.HARD_MODE_WIN_AWARD
    turn_state = game_service.get_turn_state(session)
    assert turn_state is not None
    assert turn_state.next_starter_id is None


def test_refinish_of_a_normal_game_awards_one_win(session: Session) -> None:
    game = _unsolved(
        session, hard_mode=False, hard_mode_turn=None, current_stage=PixelStage.STAGE_5
    )
    refinish(session, game, winner_id=WINNER)
    winner = session.get(Player, WINNER)
    assert winner is not None
    assert winner.wins == 1


def test_refinish_rejects_a_game_that_is_not_unsolved(session: Session) -> None:
    game = _unsolved(session, status=GameStatus.WON)
    with pytest.raises(ValueError, match="status"):
        refinish(session, game, winner_id=WINNER)
