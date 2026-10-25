"""The /skip menu: its first row opens the turn to everyone (what `/skip`
alone used to do), and the rows after it hand the turn to a player, as
`/skip @user` does. Only whoever holds the turn can use it:

- `skp:<skipper>:o`: open the turn to everyone.
- `skp:<skipper>:t:<player>`: hand the turn to that player.
- `skp:<skipper>:p:<page>`: a page of the menu.
- `skp:<skipper>:x`: cancel.

A tap re-runs every /skip check, so a menu left over from a turn that has
moved on is refused as stale; and each menu message acts once
(services/used_menus.py), so a double tap can't act twice."""

from loguru import logger
from sqlalchemy.orm import Session
from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.game_flow import skip_turn
from nani_pix_bot.commands.helpers import player_picker
from nani_pix_bot.commands.helpers.player_picker import Picker, PickerSpec, Tap, View
from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import timers as timeout_module
from nani_pix_bot.models.turn_state import TurnState
from nani_pix_bot.services import i18n, players, settings, used_menus
from nani_pix_bot.services.cosmetics import flair

PREFIX = "skp:"
SPEC = PickerSpec(PREFIX, "/skip")
_OPEN = "o"


def menu_view(session: Session, skipper_id: int, bot_id: int, page: int) -> View:
    lang = settings.get_language(session)
    at = Picker(PREFIX, skipper_id)
    found = player_picker.entries(session, skip_turn.candidates(session, skipper_id, bot_id))
    rows = player_picker.picker_keyboard(found, page, at.data(), lang).inline_keyboard
    open_row = (
        InlineKeyboardButton(i18n.t("skip.open_button", lang), callback_data=at.own(_OPEN)),
    )
    return i18n.t("skip.pick_player", lang), InlineKeyboardMarkup((open_row, *rows))


async def _page(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, tap: Tap) -> None:
    with session_scope(context.bot_data["session_factory"]) as session:
        view = menu_view(session, tap.picker.opener_id, context.bot.id, tap.number)
    await player_picker.turn_page(query, SPEC, tap.number, view)


def _usable(session: Session, query: CallbackQuery, tap: Tap, bot_id: int) -> bool:
    """Whether the tap may act: the turn is still the skipper's, a picked
    player is one the menu offers, and the menu hasn't acted yet."""
    skipper_id = tap.picker.opener_id
    if skip_turn.refusal_key(session, skipper_id) is not None:
        logger.warning("refused a stale /skip menu")
        return False
    if tap.action == "t" and tap.number not in skip_turn.candidates(session, skipper_id, bot_id):
        logger.warning("refused a stale /skip pick", target_id=tap.number)
        return False
    message = query.message
    if message is not None and not used_menus.claim(session, message.chat.id, message.message_id):
        logger.warning("refused a second tap on a used /skip menu", message_id=message.message_id)
        return False
    return True


def _act(
    session: Session, context: ContextTypes.DEFAULT_TYPE, tap: Tap
) -> tuple[str, TurnState | None]:
    """Move the turn on as tapped: the confirmation, and the turn state when
    the turn was opened (its idle autostart is armed once this commits)."""
    lang = settings.get_language(session)
    if tap.action != "t":
        logger.info("chose to open the turn to everyone from the /skip menu")
        return i18n.t("skip.opened", lang), skip_turn.open_turn(context, session)
    logger.info(
        "picked {target} to hand the turn to",
        target=players.describe_player_id(session, tap.number),
        target_id=tap.number,
    )
    skip_turn.hand_turn(context, session, tap.number)
    name = flair.named(
        session, tap.number, players.display_name(session, tap.number, unmasked=True)
    )
    return i18n.t("skip.passed", lang, username=name), None


async def _choose(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, tap: Tap) -> None:
    with session_scope(context.bot_data["session_factory"]) as session:
        usable = _usable(session, query, tap, context.bot.id)
        reply, turn_state = _act(session, context, tap) if usable else ("", None)
    if not usable:
        await player_picker.refuse_stale(query, context)
        return
    # Committed above: the idle autostart is armed only once the opened turn
    # is durable, as /skip alone always did.
    if turn_state is not None:
        timeout_module.schedule_idle_autostart(context.job_queue, turn_state)
    await query.answer()
    await player_picker.edit(query, (reply, None))


async def skip_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    read = await player_picker.read_tap(update, context, SPEC)
    if read is None:
        return
    query, tap = read
    if tap.action == "x":
        await player_picker.cancel(query, context, SPEC)
    elif tap.action == "p":
        await _page(query, context, tap)
    elif tap.action == "t" or tap.rest == (_OPEN,):
        await _choose(query, context, tap)
    else:
        await player_picker.malformed(query, SPEC)
