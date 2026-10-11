"""The shop's tile-grid message: shown to the round's first tile buyer, taken
down once the tile is chosen."""

from loguru import logger
from telegram import CallbackQuery
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.commands.shop.keyboards import tile_keyboard
from nani_pix_bot.services import i18n


async def open_grid(
    context: ContextTypes.DEFAULT_TYPE, query: CallbackQuery, user_id: int, game_id: int, lang: str
) -> None:
    """No charge: just show the 8x8 grid to pick the round's tile."""
    logger.info("opened the tile grid to pick the round's tile", game_id=game_id)
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=i18n.t("shop.tile_pick", lang),
            reply_markup=tile_keyboard(game_id),
        )
    except TelegramError:
        logger.exception("could not DM the tile grid", game_id=game_id)
    await query.answer()


async def clear_grid(query: CallbackQuery) -> None:
    """Take the buttons off the grid message the buyer tapped: the round's
    tile is chosen, so its other cells are no longer valid."""
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except BadRequest as error:
        if "not modified" not in str(error).lower():
            logger.warning("could not clear the tile grid: {error}", error=error)
    except TelegramError:
        logger.opt(exception=True).warning("could not clear the tile grid")
