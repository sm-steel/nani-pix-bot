from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from nani_pix_bot.models.player import Player
from nani_pix_bot.models.turn_state import TurnState
from nani_pix_bot.services import game as game_service


def test_get_turn_state_returns_none_when_no_row_exists(session: Session) -> None:
    assert game_service.get_turn_state(session) is None


def test_set_next_starter_creates_the_row_if_missing(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()

    game_service.set_next_starter(session, 1)
    session.commit()

    turn_state = session.get(TurnState, 1)
    assert turn_state is not None
    assert turn_state.next_starter_id == 1


def test_set_next_starter_can_open_the_turn(session: Session) -> None:
    session.add(TurnState(id=1, next_starter_id=1))
    session.commit()

    game_service.set_next_starter(session, None)
    session.commit()

    turn_state = session.get(TurnState, 1)
    assert turn_state is not None
    assert turn_state.next_starter_id is None


def test_set_next_starter_schedules_reminder_and_expiry_for_a_real_user(
    session: Session,
) -> None:
    session.add(Player(telegram_user_id=2))
    session.commit()
    before = datetime.now(UTC).replace(tzinfo=None)

    turn_state = game_service.set_next_starter(session, 2)
    session.commit()

    assert turn_state.next_starter_id == 2
    assert turn_state.reminder_at is not None
    assert turn_state.expiry_at is not None
    reminder_delta = (turn_state.reminder_at - before).total_seconds()
    expiry_delta = (turn_state.expiry_at - before).total_seconds()
    assert reminder_delta == pytest.approx(game_service.TURN_REMINDER_DELAY.total_seconds(), abs=5)
    assert expiry_delta == pytest.approx(game_service.TURN_EXPIRY_DELAY.total_seconds(), abs=5)


def test_set_next_starter_clears_reminder_and_expiry_when_opened(session: Session) -> None:
    session.add(Player(telegram_user_id=2))
    session.add(
        TurnState(
            id=1,
            next_starter_id=2,
            reminder_at=datetime.now(UTC),
            expiry_at=datetime.now(UTC),
        )
    )
    session.commit()

    turn_state = game_service.set_next_starter(session, None)
    session.commit()

    assert turn_state.next_starter_id is None
    assert turn_state.reminder_at is None
    assert turn_state.expiry_at is None


def test_clear_turn_timers_nulls_reminder_and_expiry(session: Session) -> None:
    session.add(Player(telegram_user_id=2))
    session.add(
        TurnState(
            id=1,
            next_starter_id=2,
            reminder_at=datetime.now(UTC),
            expiry_at=datetime.now(UTC),
        )
    )
    session.commit()

    game_service.clear_turn_timers(session)
    session.commit()

    turn_state = game_service.get_turn_state(session)
    assert turn_state is not None
    assert turn_state.reminder_at is None
    assert turn_state.expiry_at is None
