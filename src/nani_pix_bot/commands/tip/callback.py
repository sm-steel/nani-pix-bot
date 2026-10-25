"""Taps on the /tip menus (see views.py for the callback data)."""

from loguru import logger
from telegram import CallbackQuery, Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers import player_picker
from nani_pix_bot.commands.helpers.player_picker import Tap, View
from nani_pix_bot.commands.tip import views
from nani_pix_bot.commands.tip.sending import (
    Menu,
    StaleMenuError,
    TipRequest,
    refusal_reply,
    tip_reply,
)
from nani_pix_bot.db import session_scope
from nani_pix_bot.services import players
from nani_pix_bot.services.economy import tips


async def _page(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, tap: Tap) -> None:
    with session_scope(context.bot_data["session_factory"]) as session:
        view = views.players_view(session, tap.picker, context.bot.id, tap.number)
    await player_picker.turn_page(query, views.SPEC, tap.number, view)


def _amounts(context: ContextTypes.DEFAULT_TYPE, tipper_id: int, recipient_id: int) -> View:
    """The amount buttons, or the insufficient reply when none is affordable."""
    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        view = views.amount_view(session, tipper_id, recipient_id)
    if view is None:
        return refusal_reply(session_factory, tipper_id, tips.TipRefusal.INSUFFICIENT), None
    return view


def _menu(query: CallbackQuery) -> Menu | None:
    message = query.message
    return None if message is None else Menu(message.chat.id, message.message_id)


def _log_pick(context: ContextTypes.DEFAULT_TYPE, recipient_id: int, amount: int) -> None:
    with session_scope(context.bot_data["session_factory"]) as session:
        recipient = players.describe_player_id(session, recipient_id)
    log = {"recipient": recipient, "recipient_id": recipient_id, "amount": amount}
    if amount:
        logger.info("chose to tip {recipient} {amount} 💠", **log)
    else:
        logger.info("picked {recipient} to tip", **log)


async def _pick(
    query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, tap: Tap, amount: int
) -> None:
    """Tip, or show the amount buttons; the answer comes after the work, so the
    spinner covers the transfer."""
    recipient_id = tap.number
    _log_pick(context, recipient_id, amount)
    if not amount:
        view = _amounts(context, tap.picker.opener_id, recipient_id)
    else:
        request = TipRequest(amount, player_id=recipient_id, menu=_menu(query))
        try:
            view = (tip_reply(context, query.from_user, request), None)
        except StaleMenuError:
            await player_picker.refuse_stale(query, context)
            return
    await query.answer()
    await player_picker.edit(query, view)


async def tip_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    read = await player_picker.read_tap(update, context, views.SPEC)
    if read is None:
        return
    query, tap = read
    amount = player_picker.number(tap.picker.state[0])
    if amount is None or tap.action == "":
        await player_picker.malformed(query, views.SPEC)
    elif tap.action == "x":
        await player_picker.cancel(query, context, views.SPEC)
    elif tap.action == "p":
        await _page(query, context, tap)
    else:
        await _pick(query, context, tap, amount)
