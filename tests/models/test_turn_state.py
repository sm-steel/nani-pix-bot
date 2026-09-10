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
