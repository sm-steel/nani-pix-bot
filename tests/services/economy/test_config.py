import pytest
from sqlalchemy.orm import Session

from nani_pix_bot.models import PixelConfig
from nani_pix_bot.services.economy import config
from nani_pix_bot.services.economy.config import EconomyKey


def test_get_amounts_returns_spec_defaults_with_no_overrides(session: Session) -> None:
    amounts = config.get_amounts(session)

    assert amounts[EconomyKey.STARTING_BALANCE] == 50
    assert amounts[EconomyKey.WRONG_GUESS] == 2
    assert amounts[EconomyKey.WRONG_GUESS_CAP] == 10
    assert amounts[EconomyKey.FIRST_GUESS] == 5
    assert [amounts[k] for k in config.WIN_STAGE_KEYS] == [40, 30, 25, 20, 15]
    assert amounts[EconomyKey.SETTER] == 15
    assert amounts[EconomyKey.PROMPT_TURN] == 10
    assert amounts[EconomyKey.STREAK] == 10
    assert set(amounts) == set(EconomyKey)


def test_set_amount_overrides_one_key_only(session: Session) -> None:
    config.set_amount(session, EconomyKey.WRONG_GUESS, 3)
    session.commit()

    amounts = config.get_amounts(session)
    assert amounts[EconomyKey.WRONG_GUESS] == 3
    assert amounts[EconomyKey.FIRST_GUESS] == 5


def test_set_amount_updates_an_existing_override(session: Session) -> None:
    config.set_amount(session, EconomyKey.STREAK, 20)
    config.set_amount(session, EconomyKey.STREAK, 0)
    session.commit()

    assert config.get_amounts(session)[EconomyKey.STREAK] == 0
    assert session.query(PixelConfig).count() == 1


def test_set_amount_rejects_negative(session: Session) -> None:
    with pytest.raises(ValueError, match="negative"):
        config.set_amount(session, EconomyKey.STREAK, -1)


def test_get_amounts_ignores_unknown_stored_keys(session: Session) -> None:
    session.add(PixelConfig(key="from_a_future_phase", value=99))
    session.commit()

    assert "from_a_future_phase" not in config.get_amounts(session)
