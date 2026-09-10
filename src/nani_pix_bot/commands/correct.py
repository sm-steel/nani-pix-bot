"""The /correct command — the author-override path when the fuzzy
matcher misses a genuinely correct guess. See MECHANICS.md's "Winning"
section."""

from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands import timeout as timeout_module
from nani_pix_bot.commands.helpers.scoping import is_game_topic
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, settings


def _title(game) -> str:
    return game.title_english or game.title_romaji or game.title_native or "?"


async def correct_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if message is None or user is None:
        return

    group_chat_id = context.bot_data["group_chat_id"]
    game_topic_id = context.bot_data["game_topic_id"]
    if not is_game_topic(update, group_chat_id=group_chat_id, game_topic_id=game_topic_id):
        return

    session_factory = context.bot_data["session_factory"]

    if not context.args:
        with session_scope(session_factory) as session:
            lang = settings.get_language(session)
        await message.reply_text(i18n.t("correct.usage", lang))
        return
    target_username = context.args[0].lstrip("@")

    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        game = game_service.active_or_setup_game(session)
        if game is None or game.status != GameStatus.ACTIVE:
            await message.reply_text(i18n.t("correct.no_game", lang))
            return
        if game.starter_id != user.id:
            await message.reply_text(i18n.t("correct.not_starter", lang))
            return
        if game.total_guess_count == 0:
            await message.reply_text(i18n.t("correct.no_guesses_yet", lang))
            return
        if game.original_file_id is None:
            return  # shouldn't happen for an ACTIVE game — defensive guard

        target = game_service.find_player_by_username(session, target_username)
        if target is None:
            await message.reply_text(
                i18n.t("correct.unknown_username", lang, username=target_username)
            )
            return

        game_service.force_win(session, game, winner_id=target.telegram_user_id)
        timeout_module.cancel_timeout(context.job_queue, game.id)
        turn_state = game_service.get_turn_state(session)
        if turn_state is not None:
            timeout_module.schedule_turn_timers(context.job_queue, turn_state)
        await context.bot.send_photo(
            chat_id=group_chat_id,
            message_thread_id=game_topic_id,
            photo=game.original_file_id,
            caption=i18n.t("correct.caption", lang, winner=target_username, title=_title(game)),
        )
        game_service.clear_original_screenshot(game)
