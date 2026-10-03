from sqlalchemy.orm import Session

from nani_pix_bot.models import PixelConfig, PixelTransaction, Player
from nani_pix_bot.models.enums import PixelReason


def test_player_pixels_default_to_zero(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()

    player = session.get(Player, 1)
    assert player is not None
    assert player.pixels == 0


def test_pixel_transaction_round_trips_reason_as_its_value(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.add(PixelTransaction(player_id=1, amount=5, reason=PixelReason.FIRST_GUESS))
    session.commit()

    row = session.query(PixelTransaction).one()
    assert row.reason == PixelReason.FIRST_GUESS
    assert row.reason == "first_guess"
    assert row.game_id is None
    assert row.created_at is not None


def test_pixel_config_stores_key_value(session: Session) -> None:
    session.add(PixelConfig(key="wrong_guess", value=3))
    session.commit()

    row = session.get(PixelConfig, "wrong_guess")
    assert row is not None
    assert row.value == 3
