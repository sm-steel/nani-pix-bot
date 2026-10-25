"""Send a player's profile card as a new photo message: `/profile [@user]`
and a pick from the 👥 Someone else picker (someone_else_picker.py)."""

from dataclasses import dataclass

from loguru import logger
from telegram import Message
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.commands.profile import browse, card
from nani_pix_bot.db import session_scope
from nani_pix_bot.services import settings


@dataclass(frozen=True)
class CardRequest:
    viewer_id: int  # who asked, and the only one who may tap the card
    target_id: int
    private: bool  # in a DM with the bot
    quote: bool = True  # reply to `message`, or just send beside it


async def send_card(
    message: Message, context: ContextTypes.DEFAULT_TYPE, request: CardRequest
) -> None:
    viewing = browse.Viewing(request.viewer_id, request.target_id, private=request.private)
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        view, headline = card.load(session, request.target_id, lang)
        roster = browse.roster(session, context, request.viewer_id)
        keyboard = browse.card_keyboard(roster, viewing, lang)
    png, caption = await card.photo(context, view, headline, lang)
    try:
        await message.reply_photo(
            photo=png, caption=caption, reply_markup=keyboard, do_quote=request.quote
        )
    except TelegramError:
        logger.opt(exception=True).error(
            "failed to send the profile card of {target}",
            target=view.name,
            target_id=request.target_id,
        )
        return
    logger.info(
        "showed the /profile of {target}",
        target=view.name,
        target_id=request.target_id,
        seasons=view.seasons_played,
    )
