import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nani_pix_bot.models import CurrencyConfig, CurrencyTransfer, Player
from nani_pix_bot.models.enums import CurrencyParty, CurrencyReason


def _players(session: Session, *ids: int) -> None:
    for user_id in ids:
        session.add(Player(telegram_user_id=user_id))
    session.flush()


def _transfer(**overrides: object) -> CurrencyTransfer:
    fields: dict[str, object] = {
        "from_type": CurrencyParty.HOUSE,
        "to_type": CurrencyParty.PLAYER,
        "to_player_id": 1,
        "amount": 5,
        "reason": CurrencyReason.FIRST_GUESS,
    }
    fields.update(overrides)
    return CurrencyTransfer(**fields)


def test_player_currency_default_to_zero(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()

    player = session.get(Player, 1)
    assert player is not None
    assert player.currency == 0


def test_currency_transfer_round_trips_house_to_player(session: Session) -> None:
    _players(session, 1)
    session.add(_transfer())
    session.commit()

    row = session.query(CurrencyTransfer).one()
    assert row.from_type == CurrencyParty.HOUSE
    assert row.from_player_id is None
    assert row.to_type == "player"
    assert row.to_player_id == 1
    assert row.reason == "first_guess"
    assert row.game_id is None
    assert row.reverses_id is None
    assert row.created_at is not None


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"amount": 0}, "ck_currency_transfers_amount_positive"),
        ({"to_player_id": None}, "ck_currency_transfers_to_player"),
        ({"from_player_id": 1}, "ck_currency_transfers_from_player"),
        (
            {"from_type": CurrencyParty.POT, "to_type": CurrencyParty.HOUSE, "to_player_id": None},
            "ck_currency_transfers_pot_has_game",
        ),
        (
            {"to_type": CurrencyParty.HOUSE, "to_player_id": None},
            "ck_currency_transfers_distinct_sides",
        ),
        (
            {
                "from_type": CurrencyParty.PLAYER,
                "from_player_id": 1,
                "to_type": CurrencyParty.PLAYER,
                "to_player_id": 1,
            },
            "ck_currency_transfers_distinct_sides",
        ),
        ({"from_type": "bank"}, "ck_currency_transfers_from_type_valid"),
        ({"to_type": "bank", "to_player_id": None}, "ck_currency_transfers_to_type_valid"),
    ],
    ids=[
        "zero-amount",
        "player-without-id",
        "house-with-player-id",
        "pot-without-game",
        "house-to-house",
        "same-player",
        "unknown-from-type",
        "unknown-to-type",
    ],
)
def test_currency_transfer_check_constraints_reject_bad_rows(
    session: Session, overrides: dict[str, object], constraint: str
) -> None:
    _players(session, 1)
    session.add(_transfer(**overrides))

    with pytest.raises(IntegrityError) as exc:
        session.flush()
    assert constraint in str(exc.value)


def test_currency_transfer_allows_player_to_other_player(session: Session) -> None:
    _players(session, 1, 2)
    session.add(
        _transfer(
            from_type=CurrencyParty.PLAYER,
            from_player_id=1,
            to_type=CurrencyParty.PLAYER,
            to_player_id=2,
        )
    )
    session.flush()


def test_currency_transfer_reverses_id_is_unique(session: Session) -> None:
    _players(session, 1)
    original = _transfer()
    session.add(original)
    session.flush()
    session.add(_transfer(reverses_id=original.id))
    session.flush()
    session.add(_transfer(reverses_id=original.id))

    with pytest.raises(IntegrityError):
        session.flush()


def test_currency_config_stores_key_value(session: Session) -> None:
    session.add(CurrencyConfig(key="wrong_guess", value=3))
    session.commit()

    row = session.get(CurrencyConfig, "wrong_guess")
    assert row is not None
    assert row.value == 3
