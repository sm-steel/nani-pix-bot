from sqlalchemy.orm import Session

from nani_pix_bot.models.bot_settings import BotSettings


def test_bot_settings_defaults_to_english(session: Session) -> None:
    session.add(BotSettings(id=1))
    session.commit()

    fetched = session.get(BotSettings, 1)

    assert fetched is not None
    assert fetched.language == "EN"


def test_bot_settings_language_can_be_set(session: Session) -> None:
    session.add(BotSettings(id=1, language="RU"))
    session.commit()

    fetched = session.get(BotSettings, 1)

    assert fetched is not None
    assert fetched.language == "RU"


def test_bot_settings_games_enabled_defaults_to_true(session: Session) -> None:
    session.add(BotSettings(id=1))
    session.commit()

    fetched = session.get(BotSettings, 1)

    assert fetched is not None
    assert fetched.games_enabled is True


def test_bot_settings_games_enabled_can_be_set_false(session: Session) -> None:
    session.add(BotSettings(id=1, games_enabled=False))
    session.commit()

    fetched = session.get(BotSettings, 1)

    assert fetched is not None
    assert fetched.games_enabled is False
