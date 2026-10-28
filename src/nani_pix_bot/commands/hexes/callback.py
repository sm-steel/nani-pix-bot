"""Taps on the /hex pickers. Every button's data names the caster; anyone
else's tap is refused with an alert (see commands/helpers/player_picker.py).

- `hex:<caster>:p:<page>`: a page of the player picker (also ◀ Back).
- `hex:<caster>:t:<target>`: the kind picker for that player.
- `hex:<caster>:<target>:<kind>`: cast.
- `hex:<caster>:x`: cancel."""

from loguru import logger
from telegram import CallbackQuery, Update
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers import player_picker
from nani_pix_bot.commands.helpers.player_picker import PickerSpec, Tap
from nani_pix_bot.commands.hexes import views
from nani_pix_bot.commands.hexes.casting import Cast, cast_hex
from nani_pix_bot.commands.hexes.common import PREFIX, parse_kind
from nani_pix_bot.db import session_scope
from nani_pix_bot.services import players, settings

SPEC = PickerSpec(PREFIX, "/hex", not_yours_key="hex.not_yours", cancelled_key="hex.cancelled")


def _cast_of(tap: Tap) -> Cast | None:
    """The cast a `<target>:<kind>` tail asks for, or None."""
    if len(tap.rest) != 2:
        return None
    target_id, kind = player_picker.number(tap.rest[0]), parse_kind(tap.rest[1])
    return None if target_id is None or kind is None else Cast(target_id, kind)


def _private(query: CallbackQuery) -> bool:
    message = query.message
    return message is not None and message.chat.type == ChatType.PRIVATE


async def _page(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, tap: Tap) -> None:
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        view = views.start_view(session, tap.picker.opener_id, tap.number, lang)
    await player_picker.turn_page(query, SPEC, tap.number, view)


async def _target(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, tap: Tap) -> None:
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        view = views.kind_view(session, tap.picker.opener_id, tap.number, lang)
        target = players.describe_player_id(session, tap.number)
    logger.info("picked {target} to hex", target=target, target_id=tap.number)
    await query.answer()
    await player_picker.edit(query, view)


async def _close_picker(query: CallbackQuery, text: str) -> None:
    """A DM picker turns into the confirmation; a topic picker goes away."""
    try:
        if _private(query):
            await query.edit_message_text(text)
        else:
            await query.delete_message()
    except TelegramError as error:
        logger.warning("couldn't close the hex picker: {error}", error=str(error))


async def _cast(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, cast: Cast) -> None:
    reply = await cast_hex(context, query.from_user, cast)
    if not reply.ok:
        await query.answer(reply.text, show_alert=True)
        return
    await query.answer()
    await _close_picker(query, reply.text)


async def hex_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    read = await player_picker.read_tap(update, context, SPEC)
    if read is None:
        return
    query, tap = read
    if tap.action == "x":
        await player_picker.cancel(query, context, SPEC)
    elif tap.action == "p":
        await _page(query, context, tap)
    elif tap.action == "t":
        await _target(query, context, tap)
    elif (cast := _cast_of(tap)) is not None:
        await _cast(query, context, cast)
    else:
        await player_picker.malformed(query, SPEC)
