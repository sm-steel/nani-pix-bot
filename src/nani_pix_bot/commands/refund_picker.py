"""The /refund menu: `/refund` with no @username lists the players who hold a
refundable clue purchase, most active first, and a pick opens that player's
purchase list (refund_views.py), exactly as `/refund @user` does. Callback data:

- `rfp:<admin>:t:<player>`: open that player's purchase list.
- `rfp:<admin>:p:<page>`: a page of the menu.
- `rfp:<admin>:x`: cancel.

A pick re-checks the admin's rights, and each menu message acts once
(services/used_menus.py)."""

from loguru import logger
from sqlalchemy.orm import Session
from telegram import CallbackQuery, Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers import player_picker
from nani_pix_bot.commands.helpers.player_picker import Picker, PickerSpec, Tap, View
from nani_pix_bot.commands.refund_views import page_view
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import i18n, players, settings, used_menus
from nani_pix_bot.services.clues import shop

PREFIX = "rfp:"
SPEC = PickerSpec(PREFIX, "/refund")


def _view(session: Session, at: Picker, page: int) -> View:
    lang = settings.get_language(session)
    candidates = players.by_activity(session, shop.refundable_player_ids(session), None)
    found = player_picker.entries(session, candidates)
    if not found:
        return i18n.t("refund.no_one", lang), None
    return i18n.t("refund.pick_player", lang), player_picker.picker_keyboard(
        found, page, at.data(), lang
    )


def open_view(session: Session, admin_id: int) -> View:
    view = _view(session, Picker(PREFIX, admin_id), 0)
    logger.info("opened the /refund picker", has_targets=view[1] is not None)
    return view


def _purchase_list(
    query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, tap: Tap
) -> View | None:
    """The picked player's purchase list, or None for a stale or used menu."""
    target_id = tap.number
    with session_scope(context.bot_data["session_factory"]) as session:
        player = session.get(Player, target_id)
        if player is None or target_id not in shop.refundable_player_ids(session):
            logger.warning("refused a stale /refund pick", target_id=target_id)
            return None
        message = query.message
        if message is not None and not used_menus.claim(
            session, message.chat.id, message.message_id
        ):
            logger.warning("refused a second tap on a used /refund menu")
            return None
        logger.info(
            "picked {target} to refund",
            target=players.describe_player_id(session, target_id),
            target_id=target_id,
        )
        return page_view(session, player, 0, settings.get_language(session))


async def _pick(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, tap: Tap) -> None:
    if not await player_picker.still_admin(query, context, SPEC):
        return
    view = _purchase_list(query, context, tap)
    if view is None:
        await player_picker.refuse_stale(query, context)
        return
    await query.answer()
    await player_picker.edit(query, view)


async def refund_picker_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    read = await player_picker.read_tap(update, context, SPEC)
    if read is None:
        return
    query, tap = read
    if tap.action == "x":
        await player_picker.cancel(query, context, SPEC)
    elif tap.action == "p":
        with session_scope(context.bot_data["session_factory"]) as session:
            view = _view(session, tap.picker, tap.number)
        await player_picker.turn_page(query, SPEC, tap.number, view)
    elif tap.action == "t":
        await _pick(query, context, tap)
    else:
        await player_picker.malformed(query, SPEC)
