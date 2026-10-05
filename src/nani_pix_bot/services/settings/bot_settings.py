"""Bot-wide settings (the RU/EN language, and whether starting new
games is currently allowed) — a singleton row, same pattern as
services/game/turns.py's TurnState handling."""

from loguru import logger
from sqlalchemy.orm import Session

from nani_pix_bot.models.bot_settings import BotSettings
from nani_pix_bot.services.quiet_hours import QuietHours, parse_timezone

SETTINGS_ID = 1
DEFAULT_LANGUAGE = "EN"
DEFAULT_GAMES_ENABLED = True


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
    # DEBUG: the admin command that calls this logs the change at INFO,
    # naming the admin.
    logger.debug("Bot language set to {}", language)


def get_games_enabled(session: Session) -> bool:
    # Not logged — see get_language's note; this is checked on every DM
    # photo, just like get_language is on every update.
    settings = session.get(BotSettings, SETTINGS_ID)
    return settings.games_enabled if settings is not None else DEFAULT_GAMES_ENABLED


def set_games_enabled(session: Session, enabled: bool) -> None:
    settings = session.get(BotSettings, SETTINGS_ID)
    if settings is None:
        settings = BotSettings(id=SETTINGS_ID, games_enabled=enabled)
        session.add(settings)
    else:
        settings.games_enabled = enabled
    # DEBUG: the admin command that calls this logs the change at INFO,
    # naming the admin.
    logger.debug("Starting new games {}", "enabled" if enabled else "disabled")


DEFAULT_AUTOSTART_ENABLED = False


def get_autostart_enabled(session: Session) -> bool:
    """Whether the bot may start a game itself — see /setautostart.
    Checked in addition to get_games_enabled, not instead of it."""
    settings = session.get(BotSettings, SETTINGS_ID)
    return settings.autostart_enabled if settings is not None else DEFAULT_AUTOSTART_ENABLED


def set_autostart_enabled(session: Session, enabled: bool) -> None:
    settings = session.get(BotSettings, SETTINGS_ID)
    if settings is None:
        settings = BotSettings(id=SETTINGS_ID, autostart_enabled=enabled)
        session.add(settings)
    else:
        settings.autostart_enabled = enabled
    # DEBUG: the admin command that calls this logs the change at INFO,
    # naming the admin.
    logger.debug("Bot-initiated games {}", "enabled" if enabled else "disabled")


def get_pinned_message_id(session: Session) -> int | None:
    """The Telegram message_id of whatever "current image" is currently
    pinned in the game topic, if any — see jobs/timers.py's
    post_current_image()."""
    settings = session.get(BotSettings, SETTINGS_ID)
    return settings.pinned_message_id if settings is not None else None


def set_pinned_message_id(session: Session, message_id: int | None) -> None:
    settings = session.get(BotSettings, SETTINGS_ID)
    if settings is None:
        settings = BotSettings(id=SETTINGS_ID, pinned_message_id=message_id)
        session.add(settings)
    else:
        settings.pinned_message_id = message_id
    logger.debug("Pinned message id set to {}", message_id)


def get_quiet_hours(session: Session) -> QuietHours | None:
    """The configured quiet-hours window, or None when off — see
    /quiethours and services/quiet_hours.py."""
    # Not logged on the happy path — read by every timer callback and
    # every deadline computation (see services/game/clock.py).
    settings = session.get(BotSettings, SETTINGS_ID)
    if settings is None or settings.quiet_start is None or settings.quiet_end is None:
        return None
    tz = parse_timezone(settings.quiet_timezone or "")
    if tz is None:
        logger.error(
            "Stored quiet-hours timezone {!r} is invalid — ignoring quiet hours",
            settings.quiet_timezone,
        )
        return None
    return QuietHours(start=settings.quiet_start, end=settings.quiet_end, tz=tz)


def set_quiet_hours(session: Session, qh: QuietHours) -> None:
    settings = session.get(BotSettings, SETTINGS_ID)
    if settings is None:
        settings = BotSettings(id=SETTINGS_ID)
        session.add(settings)
    settings.quiet_start = qh.start
    settings.quiet_end = qh.end
    settings.quiet_timezone = qh.tz.key
    # DEBUG: the admin command that calls this logs the change at INFO,
    # naming the admin.
    logger.debug("Quiet hours set to {}-{} {}", qh.start, qh.end, qh.tz.key)


def clear_quiet_hours(session: Session) -> None:
    settings = session.get(BotSettings, SETTINGS_ID)
    if settings is None:
        return
    settings.quiet_start = None
    settings.quiet_end = None
    settings.quiet_timezone = None
    # DEBUG: the admin command that calls this logs the change at INFO,
    # naming the admin.
    logger.debug("Quiet hours turned off")
