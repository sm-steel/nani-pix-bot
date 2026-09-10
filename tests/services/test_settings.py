from sqlalchemy.orm import Session

from nani_pix_bot.models.bot_settings import BotSettings
from nani_pix_bot.services import settings


def test_get_language_defaults_to_en_when_no_row_exists(session: Session) -> None:
    assert settings.get_language(session) == "EN"


def test_get_language_returns_the_stored_value(session: Session) -> None:
    session.add(BotSettings(id=1, language="RU"))
    session.commit()

    assert settings.get_language(session) == "RU"


def test_set_language_creates_the_row_if_missing(session: Session) -> None:
    settings.set_language(session, "RU")
    session.commit()

    fetched = session.get(BotSettings, 1)
    assert fetched is not None
    assert fetched.language == "RU"


def test_set_language_updates_an_existing_row(session: Session) -> None:
    session.add(BotSettings(id=1, language="EN"))
    session.commit()

    settings.set_language(session, "RU")
    session.commit()

    fetched = session.get(BotSettings, 1)
    assert fetched is not None
    assert fetched.language == "RU"
