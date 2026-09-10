"""The /stop command — lets the game's own starter, or any group admin,
abort a SETUP or ACTIVE game after a Yes/No confirmation. See
MECHANICS.md's "Stopping a game" section."""

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.keyboards import (
    STOP_CANCEL_CALLBACK_DATA,
    STOP_CONFIRM_CALLBACK_DATA,
    stop_confirm_keyboard,
)
from nani_pix_bot.commands.helpers.membership import is_group_admin
from nani_pix_bot.commands.helpers.scoping import is_game_topic
from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import timers as timeout_module
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, settings


def _title(game) -> str:
    return game.title_english or game.title_romaji or game.title_native or "?"


async def _may_stop(
    context: ContextTypes.DEFAULT_TYPE, group_chat_id: int, user_id: int, game
) -> bool:
    if game.starter_id == user_id:
        return True
    return await is_group_admin(context.bot, group_chat_id, user_id)


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if message is None or user is None:
        return

    group_chat_id = context.bot_data["group_chat_id"]
    game_topic_id = context.bot_data["game_topic_id"]
    if not is_game_topic(update, group_chat_id=group_chat_id, game_topic_id=game_topic_id):
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        game = game_service.active_or_setup_game(session)
        if game is None:
            await message.reply_text(i18n.t("stop.no_game", lang))
            return

        if not await _may_stop(context, group_chat_id, user.id, game):
            logger.warning("{} tried /stop without permission on game {}", user.id, game.id)
            await message.reply_text(i18n.t("stop.not_allowed", lang))
            return

        title = _title(game)

    await message.reply_text(
        i18n.t("stop.confirm_prompt", lang, title=title),
        reply_markup=stop_confirm_keyboard(lang),
    )


async def stop_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()

    user = query.from_user
    if user is None:
        return

    group_chat_id = context.bot_data["group_chat_id"]
    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)

        if query.data == STOP_CANCEL_CALLBACK_DATA:
            await query.edit_message_text(i18n.t("stop.canceled", lang))
            return

        if query.data != STOP_CONFIRM_CALLBACK_DATA:
            return

        game = game_service.active_or_setup_game(session)
        if game is None:
            await query.edit_message_text(i18n.t("stop.no_game", lang))
            return

        if not await _may_stop(context, group_chat_id, user.id, game):
            logger.warning(
                "{} tried to confirm /stop without permission on game {}", user.id, game.id
            )
            return

        game_id = game.id
        was_active = game.status == GameStatus.ACTIVE
        if was_active:
            timeout_module.cancel_timeout(context.job_queue, game_id)
        else:
            timeout_module.cancel_setup_abandon(context.job_queue, game_id)
        session.delete(game)
        game_service.set_next_starter(session, None)
        logger.info(
            "Game {} stopped by {} (was {})", game_id, user.id, "ACTIVE" if was_active else "SETUP"
        )

    await query.edit_message_text(i18n.t("stop.confirmed", lang))
    await context.bot.send_message(
        chat_id=group_chat_id,
        message_thread_id=context.bot_data["game_topic_id"],
        text=i18n.t("stop.confirmed_group_notice", lang),
    )
