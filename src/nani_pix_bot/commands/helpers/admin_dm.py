"""The DM + group-admin gate shared by the admin commands that are only
usable in a private chat (/timezone, /quiethours, /season)."""

from dataclasses import dataclass

from loguru import logger
from telegram import Message, Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.membership import is_group_admin
from nani_pix_bot.commands.helpers.scoping import is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.services import i18n, settings


@dataclass(frozen=True)
class AdminDm:
    """A DM from a verified group admin — everything a handler needs to
    act and reply, once the shared gate in admin_dm() has passed."""

    message: Message
    user_id: int
    lang: str


async def admin_dm(
    update: Update, context: ContextTypes.DEFAULT_TYPE, command: str
) -> AdminDm | None:
    """Shared DM + admin gate: None after replying "admins only" (or
    silently, outside a DM)."""
    message = update.message
    user = update.effective_user
    if not is_private_chat(update) or message is None or user is None:
        return None
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
    if not await is_group_admin(context.bot, context.bot_data["group_chat_id"], user.id):
        logger.warning("non-admin tried /{command}", command=command)
        await message.reply_text(i18n.t("commands.admins_only", lang))
        return None
    return AdminDm(message=message, user_id=user.id, lang=lang)
