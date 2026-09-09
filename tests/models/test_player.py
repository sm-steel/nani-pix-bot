from sqlalchemy.orm import Session

from nani_pix_bot.models.player import Player


def test_player_round_trips_with_default_wins(session: Session) -> None:
    session.add(Player(telegram_user_id=42, username="frieren"))
    session.commit()

    fetched = session.get(Player, 42)

    assert fetched is not None
    assert fetched.username == "frieren"
    assert fetched.wins == 0


def test_player_username_is_optional(session: Session) -> None:
    session.add(Player(telegram_user_id=7))
    session.commit()

    fetched = session.get(Player, 7)

    assert fetched is not None
    assert fetched.username is None
