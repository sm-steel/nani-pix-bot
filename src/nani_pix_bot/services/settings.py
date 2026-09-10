"""Bot-wide settings (currently just the RU/EN language) — a singleton
row, same pattern as services/game.py's TurnState handling."""

from loguru import logger
from sqlalchemy.orm import Session

from nani_pix_bot.models.bot_settings import BotSettings

SETTINGS_ID = 1
DEFAULT_LANGUAGE = "EN"


def get_language(session: Session) -> str:
    # Not logged — called on essentially every incoming update, so even
    # DEBUG-level noise here would drown out everything else.
    settings = session.get(BotSettings, SETTINGS_ID)
    return settings.language if settings is not None else DEFAULT_LANGUAGE


def set_language(session: Session, language: str) -> None:
    settings = session.get(BotSettings, SETTINGS_ID)
    if settings is None:
        settings = BotSettings(id=SETTINGS_ID, language=language)
        session.add(settings)
    else:
        settings.language = language
    logger.info("Bot language set to {}", language)
