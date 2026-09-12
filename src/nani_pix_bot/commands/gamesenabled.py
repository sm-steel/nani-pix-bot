"""/setgamesenabled on|off — DM-only, admin-gated. Lets an admin lock
out *starting* new games entirely (e.g. while mid stage-config tuning
via /setstageconfig or /setstage), independent of /stop (which ends a
game already in progress). Plain English, no i18n — admin power-tool,
same pragmatic call as commands/stageconfig.py."""

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.membership import is_group_admin
from nani_pix_bot.commands.helpers.scoping import is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.services import settings

_USAGE = "Usage: /setgamesenabled on|off"


async def setgamesenabled_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if not is_private_chat(update) or message is None or user is None:
        return

    group_chat_id = context.bot_data["group_chat_id"]
    if not await is_group_admin(context.bot, group_chat_id, user.id):
        logger.warning("Non-admin {} tried /setgamesenabled", user.id)
        await message.reply_text("Admins only.")
        return

    args = context.args or []
    if len(args) != 1 or args[0].lower() not in ("on", "off"):
        await message.reply_text(_USAGE)
        return
    enabled = args[0].lower() == "on"

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        settings.set_games_enabled(session, enabled)

    await message.reply_text(
        "Starting new games is now enabled." if enabled else "Starting new games is now disabled."
    )
