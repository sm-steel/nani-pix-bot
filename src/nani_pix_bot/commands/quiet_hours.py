"""/timezone and /quiethours — DM only, gated to group admins/owners.
See MECHANICS.md's "Quiet hours" section. Times are always entered in
the admin's own saved timezone (/timezone), and every reply echoes the
window back with its UTC equivalent so there's no doubt which zone it's
in."""

from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.actor import describe_user
from nani_pix_bot.commands.helpers.membership import is_group_admin
from nani_pix_bot.commands.helpers.scoping import is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.services import i18n, players, quiet_hours, settings
from nani_pix_bot.services.quiet_hours import QuietHours

SET_TIMEZONE_PREFIX = "set_timezone:"
COMMON_TIMEZONES = (
    "Europe/Kaliningrad",
    "Europe/Moscow",
    "Asia/Yekaterinburg",
    "Europe/Berlin",
    "Europe/London",
    "UTC",
)


@dataclass(frozen=True)
class _AdminDm:
    """A DM from a verified group admin — everything a handler needs to
    act and reply, once the shared gate in _admin_dm() has passed."""

    message: Message
    user_id: int
    actor: str  # describe_user() of the admin, for log lines
    lang: str


def _timezone_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(tz, callback_data=f"{SET_TIMEZONE_PREFIX}{tz}")]
            for tz in COMMON_TIMEZONES
        ]
    )


async def _admin_dm(
    update: Update, context: ContextTypes.DEFAULT_TYPE, command: str
) -> _AdminDm | None:
    """Shared DM + admin gate: None after replying "admins only" (or
    silently, outside a DM)."""
    message = update.message
    user = update.effective_user
    if not is_private_chat(update) or message is None or user is None:
        return None
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
    if not await is_group_admin(context.bot, context.bot_data["group_chat_id"], user.id):
        logger.warning("Non-admin {} tried /{}", describe_user(user), command)
        await message.reply_text(i18n.t("commands.admins_only", lang))
        return None
    return _AdminDm(message=message, user_id=user.id, actor=describe_user(user), lang=lang)


def _describe(key: str, qh: QuietHours, lang: str) -> str:
    utc_start, utc_end = quiet_hours.utc_window(qh, datetime.now(UTC))
    return i18n.t(
        key,
        lang,
        start=qh.start.strftime("%H:%M"),
        end=qh.end.strftime("%H:%M"),
        tz=qh.tz.key,
        utc_start=utc_start.strftime("%H:%M"),
        utc_end=utc_end.strftime("%H:%M"),
    )


def _timezone_set_text(tz: ZoneInfo, lang: str) -> str:
    local_time = datetime.now(tz).strftime("%H:%M")
    return i18n.t("timezone.set", lang, tz=tz.key, local_time=local_time)


async def timezone_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    dm = await _admin_dm(update, context, "timezone")
    if dm is None:
        return
    session_factory = context.bot_data["session_factory"]
    args = context.args or []

    if not args:
        with session_scope(session_factory) as session:
            current = players.get_timezone(session, dm.user_id)
        text = (
            i18n.t("timezone.current", dm.lang, tz=current.key)
            if current is not None
            else i18n.t("timezone.unset", dm.lang)
        )
        logger.info(
            "Admin {} viewed /timezone (current {})",
            dm.actor,
            current.key if current is not None else "unset",
        )
        await dm.message.reply_text(text, reply_markup=_timezone_keyboard())
        return

    tz = quiet_hours.parse_timezone(args[0])
    if tz is None:
        logger.warning("Admin {} sent unknown timezone {!r}", dm.actor, args[0])
        await dm.message.reply_text(i18n.t("timezone.invalid", dm.lang, tz=args[0]))
        return
    with session_scope(session_factory) as session:
        players.set_timezone(session, dm.user_id, tz)
    await dm.message.reply_text(_timezone_set_text(tz, dm.lang))


async def timezone_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or query.from_user is None:
        return
    await query.answer()
    user_id = query.from_user.id
    if not await is_group_admin(context.bot, context.bot_data["group_chat_id"], user_id):
        logger.warning("Non-admin {} tapped a /timezone button", describe_user(query.from_user))
        return
    tz = quiet_hours.parse_timezone(query.data.removeprefix(SET_TIMEZONE_PREFIX))
    if tz is None:
        logger.warning("Unknown timezone in callback data {!r}", query.data)
        return
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        players.set_timezone(session, user_id, tz)
    await query.edit_message_text(_timezone_set_text(tz, lang))


async def quiethours_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    dm = await _admin_dm(update, context, "quiethours")
    if dm is None:
        return
    session_factory = context.bot_data["session_factory"]
    args = context.args or []

    if not args:
        with session_scope(session_factory) as session:
            current = settings.get_quiet_hours(session)
        text = (
            _describe("quiethours.status", current, dm.lang)
            if current is not None
            else i18n.t("quiethours.off", dm.lang)
        )
        logger.info(
            "Admin {} viewed /quiethours (currently {})",
            dm.actor,
            "off" if current is None else f"{current.start}-{current.end} {current.tz.key}",
        )
        await dm.message.reply_text(text)
        return

    if len(args) == 1 and args[0].lower() == "off":
        with session_scope(session_factory) as session:
            settings.clear_quiet_hours(session)
        logger.info("Admin {} turned quiet hours off", dm.actor)
        await dm.message.reply_text(i18n.t("quiethours.cleared", dm.lang))
        return

    await _set_quiet_hours(dm, session_factory, args)


async def _set_quiet_hours(dm: _AdminDm, session_factory, args: list[str]) -> None:
    start = quiet_hours.parse_hhmm(args[0]) if len(args) == 2 else None
    end = quiet_hours.parse_hhmm(args[1]) if len(args) == 2 else None
    if start is None or end is None:
        logger.warning("Admin {} sent invalid /quiethours args {!r}", dm.actor, args)
        await dm.message.reply_text(i18n.t("quiethours.usage", dm.lang))
        return
    if start == end:
        logger.warning("Admin {} sent /quiethours with start == end", dm.actor)
        await dm.message.reply_text(i18n.t("quiethours.same_times", dm.lang))
        return
    with session_scope(session_factory) as session:
        tz = players.get_timezone(session, dm.user_id)
    if tz is None:
        logger.info("Admin {} has no timezone yet — prompting before /quiethours", dm.actor)
        await dm.message.reply_text(
            i18n.t("quiethours.need_timezone", dm.lang, start=args[0]),
            reply_markup=_timezone_keyboard(),
        )
        return
    qh = QuietHours(start=start, end=end, tz=tz)
    with session_scope(session_factory) as session:
        settings.set_quiet_hours(session, qh)
    logger.info("Admin {} set quiet hours to {}-{} {}", dm.actor, qh.start, qh.end, qh.tz.key)
    await dm.message.reply_text(_describe("quiethours.set", qh, dm.lang))
