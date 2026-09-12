from unittest.mock import AsyncMock, MagicMock

from telegram import BotCommandScopeAllPrivateChats, BotCommandScopeChat

from nani_pix_bot.commands.helpers.bot_menu import refresh_command_menu


async def test_refresh_command_menu_sets_private_and_group_scopes() -> None:
    bot = MagicMock()
    bot.set_my_commands = AsyncMock()

    await refresh_command_menu(bot, group_chat_id=555, lang="en")

    assert bot.set_my_commands.await_count == 2
    calls = bot.set_my_commands.await_args_list

    private_call = next(
        c for c in calls if isinstance(c.kwargs["scope"], BotCommandScopeAllPrivateChats)
    )
    private_commands = {c.command for c in private_call.args[0]}
    assert private_commands == {
        "start",
        "help",
        "language",
        "stop",
        "stageconfig",
        "setstageconfig",
        "setstage",
        "setgamesenabled",
    }

    group_call = next(c for c in calls if isinstance(c.kwargs["scope"], BotCommandScopeChat))
    assert group_call.kwargs["scope"].chat_id == 555
    group_commands = {c.command for c in group_call.args[0]}
    assert group_commands == {"guess", "correct", "skip", "leaderboard", "help"}
