"""/partialmatch — admin view/tune of how many matched letters reveal part of
the title on a wrong guess (issue #250). DM-only and admin-gated like /pixelconfig."""

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.membership import is_group_admin
from nani_pix_bot.commands.helpers.scoping import is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.services import i18n, settings


def _parse(args: list[str]) -> int | None:
    if len(args) != 1:
        return None
    try:
        value = int(args[0])
    except ValueError:
        return None
    return value if value >= 0 else None


async def partialmatch_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if not is_private_chat(update) or message is None or user is None:
        return
    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        current = settings.get_partial_match_min_letters(session)
    if not await is_group_admin(context.bot, context.bot_data["group_chat_id"], user.id):
        logger.warning("non-admin tried /partialmatch")
        await message.reply_text(i18n.t("commands.admins_only", lang))
        return
    args = context.args or []
    if not args:
        logger.info("viewed /partialmatch")
        await message.reply_text(i18n.t("partialmatch.current", lang, value=current))
        return
    value = _parse(args)
    if value is None:
        logger.info("sent /partialmatch with bad args {args!r} — replied with usage", args=args)
        await message.reply_text(i18n.t("partialmatch.usage", lang))
        return
    with session_scope(session_factory) as session:
        settings.set_partial_match_min_letters(session, value)
    logger.info("set the partial-match minimum to {value}", value=value)
    await message.reply_text(i18n.t("partialmatch.updated", lang, value=value))
