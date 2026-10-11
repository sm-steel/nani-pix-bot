"""/seasonpass — the player's Season Pass progress, in a DM (seasons spec §2)."""

from loguru import logger
from sqlalchemy.orm import Session
from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.achievements.common import outsider_refusal
from nani_pix_bot.commands.helpers.scoping import is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.seasons import registry
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.seasons import pass_view, schedule

PREFIX = "spass:"


def view(session: Session, player_id: int, lang: str) -> tuple[str, InlineKeyboardMarkup | None]:
    """The pass text for the open season (and its keyboard, once there is one)."""
    season = schedule.current(session)
    run = registry.get(season.run_id) if season is not None else None
    if season is None or run is None:
        return i18n.t("season_pass.none", lang), None
    return "\n".join(pass_view.view_lines(session, season, run, player_id, lang)), None


async def season_pass_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user = update.message, update.effective_user
    if message is None or user is None:
        return
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
    if not is_private_chat(update):
        logger.warning("/seasonpass outside a DM")
        await message.reply_text(i18n.t("season_pass.dm_only", lang))
        return
    refusal = await outsider_refusal(context, user.id, "/seasonpass")
    if refusal is not None:
        await message.reply_text(refusal)
        return
    with session_scope(context.bot_data["session_factory"]) as session:
        players.get_or_create_player(session, user.id, username=user.username)
        text, keyboard = view(session, user.id, lang)
    await message.reply_text(text, reply_markup=keyboard)
    logger.info("showed /seasonpass")
