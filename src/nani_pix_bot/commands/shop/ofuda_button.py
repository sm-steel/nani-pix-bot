"""The shop's "Arm an Ofuda" button: arms the tapper's Ofuda for this round
and redraws their menu so the button disappears. Private, no topic notice."""

from datetime import UTC, datetime

from loguru import logger
from telegram import CallbackQuery, InlineKeyboardMarkup, User
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.membership import is_group_member
from nani_pix_bot.commands.shop.keyboards import SHOP_OFUDA_PREFIX
from nani_pix_bot.commands.shop.menu import render_shop
from nani_pix_bot.db import session_scope
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.cosmetics import ofuda


def _arm(
    session_factory, user: User, game_id: int
) -> tuple[str, str | None, InlineKeyboardMarkup | None]:
    """One transaction: arm the tapper's Ofuda, then redraw their menu.
    Returns (alert text, menu text, menu keyboard); a refusal has no menu."""
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        buyer = players.get_or_create_player(session, user.id, username=user.username)
        game = game_service.active_or_setup_game(session)
        if game is not None and game.id != game_id:
            game = None  # a button from an older round
        try:
            ofuda.arm(session, game, user.id, datetime.now(UTC))
        except ofuda.ArmRefusedError as refused:
            return i18n.t(f"shop.ofuda_refused.{refused.refusal.value}", lang), None, None
        if game is None:
            raise RuntimeError("arm() accepted a missing game")
        text, markup = render_shop(session, game, buyer, lang)
        return i18n.t("shop.ofuda_armed", lang), text, markup


async def route_ofuda(context: ContextTypes.DEFAULT_TYPE, query: CallbackQuery, user: User) -> bool:
    """Handle `shop:ofuda:<game_id>`; False for malformed data."""
    try:
        game_id = int((query.data or "").removeprefix(SHOP_OFUDA_PREFIX))
    except ValueError:
        return False
    session_factory = context.bot_data["session_factory"]
    if not await is_group_member(context.bot, context.bot_data["group_chat_id"], user.id):
        with session_scope(session_factory) as session:
            lang = settings.get_language(session)
        logger.warning("Ofuda arming refused: not a group member")
        await query.answer(i18n.t("dm_start.not_a_member", lang), show_alert=True)
        return True
    logger.info("tapped arm Ofuda", game_id=game_id)
    alert, text, markup = _arm(session_factory, user, game_id)
    await query.answer(alert, show_alert=True)
    if text is not None:
        try:
            await query.edit_message_text(text, reply_markup=markup)
        except BadRequest:
            logger.opt(exception=True).warning("could not refresh the shop menu")
    return True
