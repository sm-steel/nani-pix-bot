"""The /stop command — DM only. Lets the game's own starter, or any group
admin, abort a SETUP or ACTIVE game after a confirmation; the outcome is
still announced in the group topic, optionally revealing what the round's
answer was. See MECHANICS.md's "Stopping a game" section."""

from dataclasses import dataclass

from loguru import logger
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.keyboards import (
    STOP_CANCEL_CALLBACK_DATA,
    STOP_CONFIRM_CALLBACK_DATA,
    STOP_REVEAL_CALLBACK_DATA,
    stop_confirm_keyboard,
)
from nani_pix_bot.commands.helpers.membership import is_group_admin
from nani_pix_bot.commands.helpers.scoping import is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import timers as timeout_module
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, settings


@dataclass(frozen=True)
class _StoppedGame:
    """What _announce_stop needs, captured while the row was still live
    — it's already deleted by the time this runs."""

    title: str
    original_bytes: bytes | None


async def _may_stop(
    context: ContextTypes.DEFAULT_TYPE, group_chat_id: int, user_id: int, game
) -> bool:
    if game.starter_id == user_id:
        return True
    return await is_group_admin(context.bot, group_chat_id, user_id)


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if not is_private_chat(update) or message is None or user is None:
        return

    group_chat_id = context.bot_data["group_chat_id"]
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

        title = game_service.display_title(game, lang)
        can_reveal = game_service.has_answer_to_reveal(game)

    await message.reply_text(
        i18n.t("stop.confirm_prompt", lang, title=title),
        reply_markup=stop_confirm_keyboard(lang, can_reveal=can_reveal),
    )


async def stop_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dispatches to the Cancel or Confirm branch — two genuinely
    different actions that happen to share this small preamble. The
    reveal button is a Confirm that also posts the answer, not a third
    action, so it shares _handle_confirm."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()

    user = query.from_user
    if user is None:
        return

    if query.data == STOP_CANCEL_CALLBACK_DATA:
        await _handle_cancel(query, context)
    elif query.data in (STOP_CONFIRM_CALLBACK_DATA, STOP_REVEAL_CALLBACK_DATA):
        reveal = query.data == STOP_REVEAL_CALLBACK_DATA
        await _handle_confirm(query, context, user, reveal=reveal)


async def _handle_cancel(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
    await query.edit_message_text(i18n.t("stop.canceled", lang))


async def _handle_confirm(query, context: ContextTypes.DEFAULT_TYPE, user, *, reveal: bool) -> None:
    """Both stop buttons land here — the permission re-check, timer
    cancellation, row deletion and turn-opening are identical; only the
    group announcement differs (see _announce_stop)."""
    group_chat_id = context.bot_data["group_chat_id"]
    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)

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
        title = game_service.display_title(game, lang)
        # Forces the deferred original_image column now, while the row is
        # still live — _announce_stop runs after this session (and the row
        # itself) is gone.
        can_reveal_now = reveal and game.original_image is not None
        original_bytes = game.original_image if can_reveal_now else None
        if reveal and not can_reveal_now:
            logger.warning("Game {}: reveal requested but no image is stored", game_id)
        if was_active:
            timeout_module.cancel_timeout(context.job_queue, game_id)
            timeout_module.cancel_inactivity_timers(context.job_queue, game_id)
        else:
            timeout_module.cancel_setup_abandon(context.job_queue, game_id)
        session.delete(game)
        game_service.set_next_starter(session, None)
    # Block closed and committed above — the row deletion and turn-open
    # are durable now regardless of whether the announcement below
    # actually reaches the group (see post_current_image's docstring).
    stopped_game = _StoppedGame(title=title, original_bytes=original_bytes)
    revealed = await _announce_stop(context, session_factory, stopped_game, lang, reveal)
    logger.info(
        "Game {} stopped by {} (was {}, answer {})",
        game_id,
        user.id,
        "ACTIVE" if was_active else "SETUP",
        "revealed" if revealed else "not revealed",
    )
    await query.edit_message_text(
        i18n.t("stop.confirmed_revealed" if revealed else "stop.confirmed", lang)
    )


async def _announce_stop(
    context: ContextTypes.DEFAULT_TYPE,
    session_factory,
    stopped_game: _StoppedGame,
    lang: str,
    reveal: bool,
) -> bool:
    """Tells the group topic the round is over, and returns whether it
    also revealed the answer — meaning the reveal message was *confirmed
    sent* (see post_current_image's docstring), not merely attempted; the
    game row is already gone by the time this runs, so there's nothing
    left to gate on a successful send either way.

    Revealing posts the un-pixelated original captioned with the title —
    the same `post_current_image` the win/unsolved/timeout reveals use, so
    it becomes the topic's pinned image too. That caption already says the
    turn is open, so the reveal path posts one message rather than a photo
    plus a duplicate notice. Falls back to the plain notice if the button
    was a stale tap on a game that no longer had its image (see the
    caller's own warning log for that case)."""
    if reveal and stopped_game.original_bytes is not None:
        sent = await timeout_module.post_current_image(
            context,
            session_factory,
            photo=stopped_game.original_bytes,
            caption=i18n.t("stop.stopped_reveal_caption", lang, title=stopped_game.title),
        )
        return sent is not None

    try:
        await context.bot.send_message(
            chat_id=context.bot_data["group_chat_id"],
            message_thread_id=context.bot_data["game_topic_id"],
            text=i18n.t("stop.confirmed_group_notice", lang),
        )
    except TelegramError as exc:
        logger.warning("Failed to send the /stop group notice: {}", exc)
    return False
