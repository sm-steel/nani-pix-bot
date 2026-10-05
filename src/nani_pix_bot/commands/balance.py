"""/balance — the caller's currency balance (pixels 💠), in DM or the game topic."""

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.scoping import is_game_topic, is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.services import i18n, settings
from nani_pix_bot.services.economy import wallet


async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if message is None or user is None:
        return
    in_topic = is_game_topic(
        update,
        group_chat_id=context.bot_data["group_chat_id"],
        game_topic_id=context.bot_data["game_topic_id"],
    )
    if not (in_topic or is_private_chat(update)):
        return

    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        amount = wallet.balance(session, user.id)
    logger.info("checked /balance: {balance} 💠", balance=amount)
    await message.reply_text(i18n.t("economy.balance", lang, amount=amount))
