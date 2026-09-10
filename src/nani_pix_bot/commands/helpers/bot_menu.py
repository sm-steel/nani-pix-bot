"""Telegram Bot-API "/" command-menu registration — shared Telegram-aware
plumbing called from both app.py's startup and /language's callback
handler, not a command handler itself."""

from loguru import logger
from telegram import Bot, BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeChat

from nani_pix_bot.services import i18n


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
        BotCommand("stop", i18n.t("commands.stop", lang)),
        BotCommand("leaderboard", i18n.t("commands.leaderboard", lang)),
        BotCommand("help", i18n.t("commands.help", lang)),
    ]
    await bot.set_my_commands(private_commands, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(group_commands, scope=BotCommandScopeChat(chat_id=group_chat_id))
    logger.info("Command menu refreshed (lang={})", lang)
