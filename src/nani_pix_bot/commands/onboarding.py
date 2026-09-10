"""/start and /help — see MECHANICS.md's overview and ARCHITECTURE.md's
command/topic model."""

from loguru import logger
from telegram import Bot, BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeChat, Update
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
        bot_username = context.bot_data.get("bot_username", "")
        await message.reply_text(
            i18n.t("onboarding.help_dm_failed", lang, bot_username=bot_username)
        )


async def refresh_command_menu(bot: Bot, *, group_chat_id: int, lang: str) -> None:
    """Registers Telegram's native "/" autocomplete menu, scoped
    separately for DM vs. the group — called on startup and again
    whenever /language changes the bot's language."""
    private_commands = [
        BotCommand("start", i18n.t("commands.start", lang)),
        BotCommand("help", i18n.t("commands.help", lang)),
        BotCommand("language", i18n.t("commands.language", lang)),
    ]
    group_commands = [
        BotCommand("guess", i18n.t("commands.guess", lang)),
        BotCommand("correct", i18n.t("commands.correct", lang)),
        BotCommand("skip", i18n.t("commands.skip", lang)),
        BotCommand("leaderboard", i18n.t("commands.leaderboard", lang)),
        BotCommand("help", i18n.t("commands.help", lang)),
    ]
    await bot.set_my_commands(private_commands, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(group_commands, scope=BotCommandScopeChat(chat_id=group_chat_id))
    logger.info("Command menu refreshed (lang={})", lang)
