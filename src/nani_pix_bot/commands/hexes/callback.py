"""Taps on the /hex pickers. Every button's data names the caster; anyone
else's tap is refused with an alert.

- `hex:<caster>:p:<page>`: a page of the player picker (also ◀ Back).
- `hex:<caster>:t:<target>`: the kind picker for that player.
- `hex:<caster>:<target>:<kind>`: cast.
- `hex:<caster>:x`: cancel."""

from dataclasses import dataclass

from loguru import logger
from telegram import CallbackQuery, Update
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.commands.achievements.common import MAX_ID
from nani_pix_bot.commands.hexes import views
from nani_pix_bot.commands.hexes.casting import Cast, cast_hex
from nani_pix_bot.commands.hexes.common import PREFIX, language, parse_kind
from nani_pix_bot.db import session_scope
from nani_pix_bot.seasons.mechanics import HexKind
from nani_pix_bot.services import i18n, players, settings


@dataclass(frozen=True)
class _Tap:
    caster_id: int
    action: str  # "p", "t", "c" (cast) or "x"
    number: int = 0  # the page, or the target
    kind: HexKind | None = None


def _number(text: str) -> int | None:
    return int(text) if text.isdecimal() and int(text) <= MAX_ID else None


def _parse_rest(caster_id: int, head: str, tail: str) -> _Tap | None:
    if head in ("p", "t"):
        number = _number(tail)
        return None if number is None else _Tap(caster_id, head, number)
    target_id, kind = _number(head), parse_kind(tail)
    return None if target_id is None or kind is None else _Tap(caster_id, "c", target_id, kind)


def _parse(data: str) -> _Tap | None:
    caster, *rest = data.removeprefix(PREFIX).split(":")
    caster_id = _number(caster)
    if caster_id is None or len(rest) not in (1, 2):
        return None
    if len(rest) == 1:
        return _Tap(caster_id, "x") if rest == ["x"] else None
    return _parse_rest(caster_id, *rest)


def _private(query: CallbackQuery) -> bool:
    message = query.message
    return message is not None and message.chat.type == ChatType.PRIVATE


async def _cancel(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, _tap: _Tap) -> None:
    logger.info("cancelled the hex picker")
    await query.answer()
    await query.edit_message_text(i18n.t("hex.cancelled", language(context)))


async def _page(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, tap: _Tap) -> None:
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        text, keyboard = views.start_view(session, tap.caster_id, tap.number, lang)
    logger.info("turned the hex picker to page {page}", page=tap.number)
    await query.answer()
    await query.edit_message_text(text, reply_markup=keyboard)


async def _target(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, tap: _Tap) -> None:
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        text, keyboard = views.kind_view(session, tap.caster_id, tap.number, lang)
        target = players.describe_player_id(session, tap.number)
    logger.info("picked {target} to hex", target=target, target_id=tap.number)
    await query.answer()
    await query.edit_message_text(text, reply_markup=keyboard)


async def _close_picker(query: CallbackQuery, text: str) -> None:
    """A DM picker turns into the confirmation; a topic picker goes away."""
    try:
        if _private(query):
            await query.edit_message_text(text)
        else:
            await query.delete_message()
    except TelegramError as error:
        logger.warning("couldn't close the hex picker: {error}", error=str(error))


async def _cast(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, tap: _Tap) -> None:
    if tap.kind is None:
        return
    reply = await cast_hex(context, query.from_user, Cast(tap.number, tap.kind))
    if not reply.ok:
        await query.answer(reply.text, show_alert=True)
        return
    await query.answer()
    await _close_picker(query, reply.text)


_TAPS = {"x": _cancel, "p": _page, "t": _target, "c": _cast}


async def hex_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or query.from_user is None:
        return
    tap = _parse(query.data)
    if tap is None:
        logger.warning("ignored a malformed /hex tap {data!r}", data=query.data)
        await query.answer()
        return
    if query.from_user.id != tap.caster_id:
        logger.warning("refused a /hex tap on someone else's picker", caster_id=tap.caster_id)
        await query.answer(i18n.t("hex.not_yours", language(context)), show_alert=True)
        return
    await _TAPS[tap.action](query, context, tap)
