"""The DM-only, members-only command that replies with one view — shared by /seasonpass and
/seasonshop."""

from collections.abc import Callable
from dataclasses import dataclass

from loguru import logger
from sqlalchemy.orm import Session
from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.achievements.common import outsider_refusal
from nani_pix_bot.commands.helpers.rich import RichTarget, send_rich
from nani_pix_bot.commands.helpers.scoping import is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.services import i18n, players, settings

View = Callable[[Session, int, str], tuple[str, InlineKeyboardMarkup | None]]


@dataclass(frozen=True)
class DmCommand:
    """A DM-only, members-only command: its name for logs, the reply used
    outside a DM, the view it shows, and whether that view is rich-message
    markdown."""

    name: str
    dm_only_key: str
    view: View
    rich: bool = False


async def reply_with_view(
    update: Update, context: ContextTypes.DEFAULT_TYPE, command: DmCommand
) -> None:
    """Replies to `command` with its view, in a DM and to a group member only."""
    message, user = update.message, update.effective_user
    if message is None or user is None:
        return
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
    if not is_private_chat(update):
        logger.warning("{command} outside a DM", command=command.name)
        await message.reply_text(i18n.t(command.dm_only_key, lang))
        return
    refusal = await outsider_refusal(context, user.id, command.name)
    if refusal is not None:
        await message.reply_text(refusal)
        return
    with session_scope(context.bot_data["session_factory"]) as session:
        players.get_or_create_player(session, user.id, username=user.username)
        text, keyboard = command.view(session, user.id, lang)
    if command.rich:
        await send_rich(context.bot, RichTarget(message.chat_id), text, keyboard)
    else:
        await message.reply_text(text, reply_markup=keyboard)
    logger.info("showed {command}", command=command.name)
