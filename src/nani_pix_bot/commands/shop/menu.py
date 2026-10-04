"""/shop and `/start shop` — the DM clue-shop menu. The group's stage
posts deep-link here (see jobs/timers/current_image.py's shop_link_url)."""

from loguru import logger
from sqlalchemy.orm import Session
from telegram import InlineKeyboardMarkup, Message, Update, User
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.membership import is_group_member
from nani_pix_bot.commands.helpers.scoping import is_private_chat
from nani_pix_bot.commands.shop.keyboards import shop_keyboard
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.clues import shop


def render_shop(
    session: Session, game: Game, buyer: Player, lang: str
) -> tuple[str, InlineKeyboardMarkup | None]:
    """The shop menu text and keyboard for `buyer` in `game`. With nothing
    left to buy there is no keyboard, just the balance line plus a note."""
    text = i18n.t("shop.menu", lang, balance=buyer.currency)
    offers = shop.offers(session, game, buyer, lang)
    if not offers:
        return f"{text}\n{i18n.t('shop.nothing_left', lang)}", None
    return text, shop_keyboard(offers, game.id, lang)


async def open_shop(message: Message, context: ContextTypes.DEFAULT_TYPE, user: User) -> None:
    """Shared by `/shop` and `/start shop`: members only, and only while a
    round is ACTIVE (the setter can't shop for their own round)."""
    session_factory = context.bot_data["session_factory"]
    if not await is_group_member(context.bot, context.bot_data["group_chat_id"], user.id):
        with session_scope(session_factory) as session:
            lang = settings.get_language(session)
        logger.warning("Shop refused for {}: not a group member", user.id)
        await message.reply_text(i18n.t("dm_start.not_a_member", lang))
        return

    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        buyer = players.get_or_create_player(session, user.id, username=user.username)
        game = game_service.active_or_setup_game(session)
        if game is None or game.status != GameStatus.ACTIVE:
            logger.debug("Shop opened by {} with no active game", user.id)
            text, markup = i18n.t("shop.no_game", lang), None
        elif game.starter_id == user.id:
            logger.debug("Shop opened by {}, the setter of game {}", user.id, game.id)
            text, markup = i18n.t("shop.setter", lang), None
        else:
            text, markup = render_shop(session, game, buyer, lang)
            logger.debug("Shop opened by {} for game {}", user.id, game.id)
    await message.reply_text(text, reply_markup=markup)


async def shop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if not is_private_chat(update) or message is None or user is None:
        return
    await open_shop(message, context, user)
