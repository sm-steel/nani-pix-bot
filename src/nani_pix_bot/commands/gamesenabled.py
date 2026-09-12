"""/setgamesenabled on|off — DM-only, admin-gated. Lets an admin lock
out *starting* new games entirely (e.g. while mid stage-config tuning
via /setstageconfig or /setstage), independent of /stop (which ends a
game already in progress)."""

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.membership import is_group_admin
from nani_pix_bot.commands.helpers.scoping import is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.services import i18n, settings


async def setgamesenabled_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if not is_private_chat(update) or message is None or user is None:
        return

    session_factory = context.bot_data["session_factory"]
    group_chat_id = context.bot_data["group_chat_id"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        if not await is_group_admin(context.bot, group_chat_id, user.id):
            logger.warning("Non-admin {} tried /setgamesenabled", user.id)
            await message.reply_text(i18n.t("commands.admins_only", lang))
            return

    args = context.args or []
    if len(args) != 1 or args[0].lower() not in ("on", "off"):
        await message.reply_text(i18n.t("gamesenabled.usage", lang))
        return
    enabled = args[0].lower() == "on"

    with session_scope(session_factory) as session:
        settings.set_games_enabled(session, enabled)

    await message.reply_text(
        i18n.t("gamesenabled.enabled", lang) if enabled else i18n.t("gamesenabled.disabled", lang)
    )
