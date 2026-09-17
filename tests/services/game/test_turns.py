from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from nani_pix_bot.models.player import Player
from nani_pix_bot.models.turn_state import TurnState
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services.game import turns


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


def test_set_next_starter_none_sets_turn_opened_and_autostart_deadline(session: Session) -> None:
    turn_state = turns.set_next_starter(session, None)

    assert turn_state.turn_opened_at is not None
    assert turn_state.autostart_deadline_at is not None
    delay = turn_state.autostart_deadline_at - turn_state.turn_opened_at
    assert delay == turns.IDLE_AUTOSTART_DELAY


def test_set_next_starter_a_real_user_clears_autostart_columns(session: Session) -> None:
    session.add(Player(telegram_user_id=5))
    session.commit()
    turns.set_next_starter(session, None)

    turn_state = turns.set_next_starter(session, 5)

    assert turn_state.turn_opened_at is None
    assert turn_state.autostart_deadline_at is None


def test_mark_turn_open_if_unassigned_arms_the_backstop_when_the_turn_is_open(
    session: Session,
) -> None:
    session.add(TurnState(id=1, next_starter_id=None))
    session.commit()

    turn_state = turns.mark_turn_open_if_unassigned(session)

    assert turn_state.turn_opened_at is not None
    assert turn_state.autostart_deadline_at is not None


def test_mark_turn_open_if_unassigned_is_a_noop_when_a_player_is_designated(
    session: Session,
) -> None:
    session.add(Player(telegram_user_id=5))
    session.add(TurnState(id=1, next_starter_id=5))
    session.commit()

    turn_state = turns.mark_turn_open_if_unassigned(session)

    assert turn_state.next_starter_id == 5
    assert turn_state.turn_opened_at is None
    assert turn_state.autostart_deadline_at is None


def test_clear_autostart_clears_both_columns(session: Session) -> None:
    now = datetime.now(UTC)
    session.add(
        TurnState(id=1, turn_opened_at=now, autostart_deadline_at=now + timedelta(hours=24))
    )
    session.commit()

    turns.clear_autostart(session)

    turn_state = session.get(TurnState, 1)
    assert turn_state is not None
    assert turn_state.turn_opened_at is None
    assert turn_state.autostart_deadline_at is None
