"""Taps on the /tip menus (see views.py for the callback data)."""

from loguru import logger
from telegram import CallbackQuery, Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers import player_picker
from nani_pix_bot.commands.helpers.player_picker import Tap, View
from nani_pix_bot.commands.tip import views
from nani_pix_bot.commands.tip.sending import TipRequest, refusal_reply, tip_reply
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


async def _pick(
    query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, tap: Tap, amount: int
) -> None:
    recipient_id = tap.number
    with session_scope(context.bot_data["session_factory"]) as session:
        recipient = players.describe_player_id(session, recipient_id)
    logger.info(
        "picked {recipient} to tip", recipient=recipient, recipient_id=recipient_id, amount=amount
    )
    await query.answer()
    if amount:
        request = TipRequest(amount, player_id=recipient_id)
        view: View = (tip_reply(context, query.from_user, request), None)
    else:
        view = _amounts(context, tap.picker.opener_id, recipient_id)
    await player_picker.edit(query, view)


async def tip_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    read = await player_picker.read_tap(update, context, views.SPEC)
    if read is None:
        return
    query, tap = read
    amount = player_picker.number(tap.picker.state[0])
    if amount is None or tap.action == "":
        logger.warning("ignored a malformed /tip tap {data!r}", data=query.data)
        await query.answer()
    elif tap.action == "x":
        await player_picker.cancel(query, context, views.SPEC)
    elif tap.action == "p":
        await _page(query, context, tap)
    else:
        await _pick(query, context, tap, amount)
