from datetime import UTC, datetime

from sqlalchemy.orm import Session

from nani_pix_bot.models.player import Player
from nani_pix_bot.models.turn_state import TurnState


def test_turn_state_next_starter_defaults_to_open(session: Session) -> None:
    session.add(TurnState(id=1))
    session.commit()

    fetched = session.get(TurnState, 1)

    assert fetched is not None
    assert fetched.next_starter_id is None
    assert fetched.reminder_at is None
    assert fetched.expiry_at is None


def test_turn_state_can_designate_a_next_starter(session: Session) -> None:
    winner = Player(telegram_user_id=5)
    session.add(winner)
    session.add(TurnState(id=1, next_starter_id=5))
    session.commit()
    session.expire_all()

    fetched = session.get(TurnState, 1)

    assert fetched is not None
    assert fetched.next_starter_id == 5


def test_turn_state_turn_opened_at_defaults_to_none(session: Session) -> None:
    session.add(TurnState(id=1))
    session.commit()

    fetched = session.get(TurnState, 1)

    assert fetched is not None
    assert fetched.turn_opened_at is None
    assert fetched.autostart_deadline_at is None


def test_turn_state_autostart_columns_can_be_set(session: Session) -> None:
    opened_at = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
    deadline = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
    session.add(TurnState(id=1, turn_opened_at=opened_at, autostart_deadline_at=deadline))
    session.commit()
    session.expire_all()

    fetched = session.get(TurnState, 1)

    assert fetched is not None
    assert fetched.turn_opened_at == opened_at.replace(tzinfo=None)
    assert fetched.autostart_deadline_at == deadline.replace(tzinfo=None)
