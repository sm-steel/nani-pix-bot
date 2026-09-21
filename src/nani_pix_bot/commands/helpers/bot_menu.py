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
        BotCommand("newgame", i18n.t("commands.newgame", lang)),
        BotCommand("language", i18n.t("commands.language", lang)),
        # Listed unconditionally, like every other command here: this menu
        # is set once at startup (and on a /language change) for all
        # private chats, so it can't be varied per player — and on a bot
        # with MAL unconfigured, /linkmal answers with "not configured"
        # rather than doing nothing, which is a better dead end than a
        # command nobody can discover.
        BotCommand("linkmal", i18n.t("commands.linkmal", lang)),
        BotCommand("unlinkmal", i18n.t("commands.unlinkmal", lang)),
        BotCommand("stop", i18n.t("commands.stop", lang)),
        BotCommand("stageconfig", i18n.t("commands.stageconfig", lang)),
        BotCommand("setstageconfig", i18n.t("commands.setstageconfig", lang)),
        BotCommand("setstage", i18n.t("commands.setstage", lang)),
        BotCommand("setgamesenabled", i18n.t("commands.setgamesenabled", lang)),
        BotCommand("setautostart", i18n.t("commands.setautostart", lang)),
        BotCommand("version", i18n.t("commands.version", lang)),
    ]
    group_commands = [
        BotCommand("guess", i18n.t("commands.guess", lang)),
        BotCommand("correct", i18n.t("commands.correct", lang)),
        BotCommand("skip", i18n.t("commands.skip", lang)),
        BotCommand("leaderboard", i18n.t("commands.leaderboard", lang)),
        BotCommand("help", i18n.t("commands.help", lang)),
        BotCommand("version", i18n.t("commands.version", lang)),
    ]
    await bot.set_my_commands(private_commands, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(group_commands, scope=BotCommandScopeChat(chat_id=group_chat_id))
    logger.info("Command menu refreshed (lang={})", lang)
