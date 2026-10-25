"""/hexes — your hex status (in or out, the hex on you, your cooldowns), and
`/hexes off` / `/hexes on` to opt out of hexes or back in."""

from datetime import datetime

from loguru import logger
from sqlalchemy.orm import Session
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update, User
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.durations import duration
from nani_pix_bot.commands.hexes import views
from nani_pix_bot.commands.hexes.common import ICONS, in_scope, kind_name, language, now, page_data
from nani_pix_bot.db import session_scope
from nani_pix_bot.seasons.mechanics import HexKind
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.seasons import hexes
from nani_pix_bot.services.seasons.hex_marks import naive_utc
from nani_pix_bot.services.seasons.hexes import HexRefusedError

_SWITCHES = {"off": True, "on": False}


def _active_line(session: Session, player_id: int, at: datetime, lang: str) -> str | None:
    row = hexes.active_hex_for(session, player_id, at)
    if row is None:
        return None
    kind = HexKind(row.kind)
    names = {"icon": ICONS[kind], "name": kind_name(row.kind, lang)}
    if row.expires_at is None:
        return i18n.t("hexes.active_armed", lang, **names)
    left = duration(row.expires_at - naive_utc(at), lang)
    return i18n.t("hexes.active", lang, left=left, **names)


def _cooldown_lines(
    session: Session, opened: hexes.OpenHexes, player_id: int, at: datetime, lang: str
) -> list[str]:
    lines = []
    for rule in opened.run.hexes:
        wait = hexes.cooldown_left(session, player_id, rule, at)
        if wait.total_seconds() > 0:
            name = kind_name(rule.kind.value, lang)
            lines.append(
                i18n.t(
                    "hexes.cooldown",
                    lang,
                    icon=ICONS[rule.kind],
                    name=name,
                    wait=duration(wait, lang),
                )
            )
    return [i18n.t("hexes.cooldowns", lang), *lines] if lines else []


def _view(session: Session, player_id: int, lang: str) -> views.View:
    at = now()
    lines = [views.status_line(session, player_id, lang)]
    opened = hexes.open_hexes(session)
    if opened is None:
        return "\n".join([*lines, i18n.t("hex.closed", lang)]), None
    active = _active_line(session, player_id, at, lang)
    lines += [active] if active is not None else []
    lines += _cooldown_lines(session, opened, player_id, at, lang)
    button = InlineKeyboardButton(
        i18n.t("hex.pick_player_button", lang), callback_data=page_data(player_id, 0)
    )
    return "\n".join(lines), InlineKeyboardMarkup([[button]])


async def _show(message: Message, context: ContextTypes.DEFAULT_TYPE, user: User) -> None:
    with session_scope(context.bot_data["session_factory"]) as session:
        text, keyboard = _view(session, user.id, settings.get_language(session))
    logger.info("viewed their hexes")
    await message.reply_text(text, reply_markup=keyboard)


def _switch(context: ContextTypes.DEFAULT_TYPE, user: User, off: bool) -> str:
    lang = language(context)
    try:
        with session_scope(context.bot_data["session_factory"]) as session:
            player = players.get_or_create_player(session, user.id, username=user.username)
            hexes.set_opt_out(session, player, off, now())
    except HexRefusedError as error:  # set_opt_out logged it
        wait = "" if error.wait is None else duration(error.wait, lang)
        return i18n.t(f"hex.refusal.{error.refusal.value}", lang, wait=wait, price=0, balance=0)
    return i18n.t("hexes.off" if off else "hexes.on", lang)


async def hexes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user = update.message, update.effective_user
    if message is None or user is None or not in_scope(update, context):
        return
    args = context.args or []
    if not args:
        await _show(message, context, user)
        return
    off = _SWITCHES.get(args[0].lower()) if len(args) == 1 else None
    if off is None:
        logger.warning("malformed /hexes: {args!r}", args=args)
        await message.reply_text(i18n.t("hexes.usage", language(context)))
        return
    await message.reply_text(_switch(context, user, off))
