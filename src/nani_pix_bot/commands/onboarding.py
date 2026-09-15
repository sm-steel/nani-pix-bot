"""/start and /help — see MECHANICS.md's overview and ARCHITECTURE.md's
command/topic model."""

from loguru import logger
from telegram import Update
from telegram.error import Forbidden
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.scoping import is_game_topic, is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.services import i18n, settings


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if not is_private_chat(update) or message is None or user is None:
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
    logger.debug("/start sent to {}", user.id)
    await message.reply_text(i18n.t("onboarding.start", lang))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if message is None or user is None:
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)

    if is_private_chat(update):
        await message.reply_text(i18n.t("onboarding.help", lang))
        return

    group_chat_id = context.bot_data["group_chat_id"]
    game_topic_id = context.bot_data["game_topic_id"]
    if not is_game_topic(update, group_chat_id=group_chat_id, game_topic_id=game_topic_id):
        return

    try:
        await context.bot.send_message(chat_id=user.id, text=i18n.t("onboarding.help", lang))
    except Forbidden:
        logger.warning("Couldn't DM /help to {} — they haven't started the bot", user.id)
        # bot_data["bot_username"] is written by app.py's _post_init, so it
        # is absent until the first getMe answers (and in tests). The old
        # "" default put a dangling "@" in the middle of the sentence,
        # which reads as a bug; dropping the handle drops the mention
        # clause instead (see correct.py/skip.py, #81, same window).
        bot_username = context.bot_data.get("bot_username")
        key = "onboarding.help_dm_failed" if bot_username else "onboarding.help_dm_failed_no_handle"
        await message.reply_text(i18n.t(key, lang, bot_username=bot_username))
