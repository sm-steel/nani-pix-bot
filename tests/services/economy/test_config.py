import pytest
from sqlalchemy.orm import Session

from nani_pix_bot.models import CurrencyConfig
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
    assert amounts[EconomyKey.CLUE_LAST_LETTER] == 10
    assert amounts[EconomyKey.CLUE_FIRST_LETTER] == 20
    assert amounts[EconomyKey.CLUE_TITLE_SHAPE] == 25
    assert amounts[EconomyKey.CLUE_SCREENSHOT] == 30
    assert amounts[EconomyKey.CLUE_SCREENSHOT_STEP] == 15
    assert amounts[EconomyKey.CLUE_TILE] == 10
    assert set(amounts) == set(EconomyKey)


def test_set_amount_overrides_one_key_only(session: Session) -> None:
    config.set_amount(session, EconomyKey.WRONG_GUESS, 3)
    session.commit()

    amounts = config.get_amounts(session)
    assert amounts[EconomyKey.WRONG_GUESS] == 3
    assert amounts[EconomyKey.FIRST_GUESS] == 5


def test_set_amount_updates_an_existing_override(session: Session) -> None:
    config.set_amount(session, EconomyKey.SETTER, 20)
    config.set_amount(session, EconomyKey.SETTER, 0)
    session.commit()

    assert config.get_amounts(session)[EconomyKey.SETTER] == 0
    assert session.query(CurrencyConfig).count() == 1


def test_set_amount_rejects_negative(session: Session) -> None:
    with pytest.raises(ValueError, match="negative"):
        config.set_amount(session, EconomyKey.SETTER, -1)


def test_get_amounts_ignores_unknown_stored_keys(session: Session) -> None:
    session.add(CurrencyConfig(key="from_a_future_phase", value=99))
    session.commit()

    assert "from_a_future_phase" not in config.get_amounts(session)


def test_set_amount_rejects_zero_clue_price(session: Session) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        config.set_amount(session, EconomyKey.CLUE_LAST_LETTER, 0)


def test_set_amount_still_accepts_zero_for_rewards(session: Session) -> None:
    config.set_amount(session, EconomyKey.FIRST_GUESS, 0)

    assert config.get_amounts(session)[EconomyKey.FIRST_GUESS] == 0
