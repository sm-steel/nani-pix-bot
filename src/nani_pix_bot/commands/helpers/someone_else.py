"""The 🔍 Someone else button under your own /profile card and /achievements
list (issue #479). It opens a player picker in a new message (every known
player but you and Nani Pix, most active first, labels unmasked), and a pick
shows that player's card or list the way `/profile @user` or
`/achievements @user` would, then the picker goes. Callback data, with the
command's own prefix:

- `<prefix><viewer>:o`: open the picker (the button itself).
- `<prefix><viewer>:t:<player>`: show that player's.
- `<prefix><viewer>:p:<page>`: a page of the picker.
- `<prefix><viewer>:x`: cancel.

Only the viewer's taps count; in DM, the group-member gate the commands
themselves apply is checked again on open and on a pick."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from loguru import logger
from sqlalchemy.orm import Session
from telegram import CallbackQuery, InlineKeyboardButton, Message, Update
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.commands.achievements.common import outsider_refusal
from nani_pix_bot.commands.helpers import player_picker
from nani_pix_bot.commands.helpers.player_picker import Picker, PickerSpec, Tap, View
from nani_pix_bot.db import session_scope
from nani_pix_bot.services import i18n, players, settings

OPEN = "o"

# Show `target`'s card or list as a new message beside `message`, for `viewer`.
Show = Callable[[Message, ContextTypes.DEFAULT_TYPE, int, int], Awaitable[None]]


@dataclass(frozen=True)
class SomeoneElse:
    """One command's Someone else button: its picker's spec, its three keys
    (`<namespace>.someone_else`, `.pick_player`, `.no_one`) and how a pick is shown."""

    spec: PickerSpec
    namespace: str  # "profile" or "achievements"
    show: Show


def button(prefix: str, namespace: str, viewer_id: int, lang: str) -> InlineKeyboardButton:
    """The 🔍 Someone else button that opens `prefix`'s picker for `viewer_id`."""
    return InlineKeyboardButton(
        i18n.t(f"{namespace}.someone_else", lang),
        callback_data=Picker(prefix, viewer_id).own(OPEN),
    )


def _candidates(session: Session, viewer_id: int, bot_id: int) -> list[int]:
    excluded = {viewer_id, bot_id}
    known = [pid for pid in players.known_ids(session) if pid not in excluded]
    return players.by_activity(session, known, None)


def _view(session: Session, which: SomeoneElse, at: Picker, bot_id: int, page: int) -> View:
    lang = settings.get_language(session)
    found = player_picker.entries(session, _candidates(session, at.opener_id, bot_id))
    if not found:
        return i18n.t(f"{which.namespace}.no_one", lang), None
    keyboard = player_picker.picker_keyboard(found, page, at.data(), lang)
    return i18n.t(f"{which.namespace}.pick_player", lang), keyboard


async def _refused(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, command: str) -> bool:
    """In DM, whether the tapper is no longer a group member (then alerted)."""
    message = query.message
    if message is None or message.chat.type != ChatType.PRIVATE:
        return False
    refusal = await outsider_refusal(context, query.from_user.id, command)
    if refusal is not None:
        await query.answer(refusal, show_alert=True)
    return refusal is not None


async def _open(
    query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, which: SomeoneElse, tap: Tap
) -> None:
    message = query.message
    if not isinstance(message, Message):
        await player_picker.malformed(query, which.spec)
        return
    with session_scope(context.bot_data["session_factory"]) as session:
        text, keyboard = _view(session, which, tap.picker, context.bot.id, 0)
    logger.info(
        "opened the {command} picker", command=which.spec.command, has_targets=keyboard is not None
    )
    await query.answer()
    await message.reply_text(text, reply_markup=keyboard, do_quote=False)


async def _pick(
    query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, which: SomeoneElse, tap: Tap
) -> None:
    viewer_id, target_id = tap.picker.opener_id, tap.number
    message = query.message
    with session_scope(context.bot_data["session_factory"]) as session:
        known = target_id in _candidates(session, viewer_id, context.bot.id)
        target = players.describe_player_id(session, target_id)
    if not known or not isinstance(message, Message):
        logger.warning("refused a stale {command} pick", command=which.spec.command)
        await player_picker.refuse_stale(query, context)
        return
    logger.info(
        "picked {target} in the {command} picker",
        command=which.spec.command,
        target=target,
        target_id=target_id,
    )
    await query.answer()
    await which.show(message, context, viewer_id, target_id)
    try:
        await query.delete_message()
    except TelegramError as error:
        logger.warning(
            "couldn't remove the {command} picker: {error}",
            command=which.spec.command,
            error=str(error),
        )


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE, which: SomeoneElse) -> None:
    read = await player_picker.read_tap(update, context, which.spec)
    if read is None:
        return
    query, tap = read
    if tap.action == "x":
        await player_picker.cancel(query, context, which.spec)
    elif tap.action == "p":
        with session_scope(context.bot_data["session_factory"]) as session:
            view = _view(session, which, tap.picker, context.bot.id, tap.number)
        await player_picker.turn_page(query, which.spec, tap.number, view)
    elif tap.rest == (OPEN,):
        if not await _refused(query, context, which.spec.command):
            await _open(query, context, which, tap)
    elif tap.action == "t":
        if not await _refused(query, context, which.spec.command):
            await _pick(query, context, which, tap)
    else:
        await player_picker.malformed(query, which.spec)
