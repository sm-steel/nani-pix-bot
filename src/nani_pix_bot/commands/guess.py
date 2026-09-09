"""The /guess command — see MECHANICS.md's "Guess matching" and
"Pixelation stages" sections."""

from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.scoping import is_game_topic
from nani_pix_bot.commands.timeout import cancel_timeout
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import pixelate as pixelate_service


def _title(game) -> str:
    return game.title_english or game.title_romaji or game.title_native or "?"


async def guess_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if message is None or user is None:
        return

    group_chat_id = context.bot_data["group_chat_id"]
    game_topic_id = context.bot_data["game_topic_id"]
    if not is_game_topic(update, group_chat_id=group_chat_id, game_topic_id=game_topic_id):
        return

    guess_text = " ".join(context.args) if context.args else ""
    if not guess_text:
        await message.reply_text("Usage: /guess <anime title>")
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        game = game_service.active_or_setup_game(session)
        if game is None or game.status != GameStatus.ACTIVE:
            await message.reply_text(
                "No game is currently running. DM me a screenshot to start one!"
            )
            return
        if game.original_file_id is None or game.current_stage is None:
            # Shouldn't happen — an ACTIVE game always has both set. Defensive
            # guard (also narrows the type for the calls below).
            return

        game_service.get_or_create_player(session, user.id, username=user.username)
        outcome = game_service.record_guess(
            session, game, guesser_id=user.id, guess_text=guess_text
        )

        if outcome is game_service.GuessOutcome.WON:
            cancel_timeout(context.job_queue, game.id)
            await context.bot.send_photo(
                chat_id=group_chat_id,
                message_thread_id=game_topic_id,
                photo=game.original_file_id,
                caption=f"🎉 Got it! It was {_title(game)}.",
            )
            game_service.clear_original_screenshot(game)
        elif outcome is game_service.GuessOutcome.STAGE_ADVANCED:
            telegram_file = await context.bot.get_file(game.original_file_id)
            original_bytes = bytes(await telegram_file.download_as_bytearray())
            pixelated = pixelate_service.pixelate(original_bytes, game.current_stage)
            await context.bot.send_photo(
                chat_id=group_chat_id, message_thread_id=game_topic_id, photo=pixelated
            )
        elif outcome is game_service.GuessOutcome.UNSOLVED:
            await context.bot.send_photo(
                chat_id=group_chat_id,
                message_thread_id=game_topic_id,
                photo=game.original_file_id,
                caption=f"Nobody guessed it. It was {_title(game)}.",
            )
            game_service.clear_original_screenshot(game)
