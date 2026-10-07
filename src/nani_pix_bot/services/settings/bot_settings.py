"""Bot-wide settings (the RU/EN language, and whether starting new
games is currently allowed) — a singleton row, same pattern as
services/game/turns.py's TurnState handling."""

from zoneinfo import ZoneInfo

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
    logger.debug("bot language set to {language}", language=language)


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
    logger.debug("starting new games {state}", state="enabled" if enabled else "disabled")


DEFAULT_PARTIAL_MATCH_MIN_LETTERS = 4
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
    logger.debug("bot-initiated games {state}", state="enabled" if enabled else "disabled")


def get_partial_match_min_letters(session: Session) -> int:
    settings = session.get(BotSettings, SETTINGS_ID)
    if settings is None:
        return DEFAULT_PARTIAL_MATCH_MIN_LETTERS
    return settings.partial_match_min_letters


def set_partial_match_min_letters(session: Session, value: int) -> None:
    if value < 0:
        msg = f"partial_match_min_letters cannot be negative: {value}"
        raise ValueError(msg)
    settings = session.get(BotSettings, SETTINGS_ID)
    if settings is None:
        session.add(BotSettings(id=SETTINGS_ID, partial_match_min_letters=value))
    else:
        settings.partial_match_min_letters = value
    # DEBUG: /partialmatch logs the change at INFO, naming the admin.
    logger.debug("partial-match minimum set to {value}", value=value)


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
    logger.debug("pinned message id set to {msg_id}", msg_id=message_id)


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
            "stored quiet-hours timezone {timezone!r} is invalid — ignoring quiet hours",
            timezone=settings.quiet_timezone,
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
    logger.debug(
        "quiet hours set to {start}-{end} {timezone}",
        start=qh.start,
        end=qh.end,
        timezone=qh.tz.key,
    )


def clear_quiet_hours(session: Session) -> None:
    settings = session.get(BotSettings, SETTINGS_ID)
    if settings is None:
        return
    settings.quiet_start = None
    settings.quiet_end = None
    settings.quiet_timezone = None
    # DEBUG: the admin command that calls this logs the change at INFO,
    # naming the admin.
    logger.debug("quiet hours turned off")


def get_group_timezone(session: Session) -> ZoneInfo:
    """The group's timezone: the quiet-hours zone when one is set, else UTC.
    Achievements count active days, and champions close periods, in it."""
    settings = session.get(BotSettings, SETTINGS_ID)
    zone = parse_timezone(settings.quiet_timezone or "") if settings is not None else None
    return zone or ZoneInfo("UTC")
