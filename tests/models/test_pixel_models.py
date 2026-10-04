import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nani_pix_bot.models import PixelConfig, PixelTransfer, Player
from nani_pix_bot.models.enums import PixelParty, PixelReason


def _players(session: Session, *ids: int) -> None:
    for user_id in ids:
        session.add(Player(telegram_user_id=user_id))
    session.flush()


def _transfer(**overrides: object) -> PixelTransfer:
    fields: dict[str, object] = {
        "from_type": PixelParty.HOUSE,
        "to_type": PixelParty.PLAYER,
        "to_player_id": 1,
        "amount": 5,
        "reason": PixelReason.FIRST_GUESS,
    }
    fields.update(overrides)
    return PixelTransfer(**fields)


def test_player_pixels_default_to_zero(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()

    player = session.get(Player, 1)
    assert player is not None
    assert player.pixels == 0


def test_pixel_transfer_round_trips_house_to_player(session: Session) -> None:
    _players(session, 1)
    session.add(_transfer())
    session.commit()

    row = session.query(PixelTransfer).one()
    assert row.from_type == PixelParty.HOUSE
    assert row.from_player_id is None
    assert row.to_type == "player"
    assert row.to_player_id == 1
    assert row.reason == "first_guess"
    assert row.game_id is None
    assert row.reverses_id is None
    assert row.created_at is not None


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount": 0},
        {"to_player_id": None},
        {"from_player_id": 1},
        {"from_type": PixelParty.POT, "to_type": PixelParty.HOUSE, "to_player_id": None},
        {"to_type": PixelParty.HOUSE, "to_player_id": None},
        {
            "from_type": PixelParty.PLAYER,
            "from_player_id": 1,
            "to_type": PixelParty.PLAYER,
            "to_player_id": 1,
        },
    ],
    ids=[
        "zero-amount",
        "player-without-id",
        "house-with-player-id",
        "pot-without-game",
        "house-to-house",
        "same-player",
    ],
)
def test_pixel_transfer_check_constraints_reject_bad_rows(
    session: Session, overrides: dict[str, object]
) -> None:
    _players(session, 1)
    session.add(_transfer(**overrides))

    with pytest.raises(IntegrityError):
        session.flush()


def test_pixel_transfer_allows_player_to_other_player(session: Session) -> None:
    _players(session, 1, 2)
    session.add(
        _transfer(
            from_type=PixelParty.PLAYER,
            from_player_id=1,
            to_type=PixelParty.PLAYER,
            to_player_id=2,
        )
    )
    session.flush()


def test_pixel_transfer_reverses_id_is_unique(session: Session) -> None:
    _players(session, 1)
    original = _transfer()
    session.add(original)
    session.flush()
    session.add(_transfer(reverses_id=original.id))
    session.flush()
    session.add(_transfer(reverses_id=original.id))

    with pytest.raises(IntegrityError):
        session.flush()


def test_pixel_config_stores_key_value(session: Session) -> None:
    session.add(PixelConfig(key="wrong_guess", value=3))
    session.commit()

    row = session.get(PixelConfig, "wrong_guess")
    assert row is not None
    assert row.value == 3
