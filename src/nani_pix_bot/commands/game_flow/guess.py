"""The /guess command — see MECHANICS.md's "Guess matching" and
"Pixelation stages" sections."""

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.scoping import is_game_topic
from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import timers as timeout_module
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services import pixelate as pixelate_service
from nani_pix_bot.services.settings import stage_config


async def guess_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if message is None or user is None:
        return

    group_chat_id = context.bot_data["group_chat_id"]
    game_topic_id = context.bot_data["game_topic_id"]
    if not is_game_topic(update, group_chat_id=group_chat_id, game_topic_id=game_topic_id):
        return

    session_factory = context.bot_data["session_factory"]

    guess_text = " ".join(context.args) if context.args else ""
    if not guess_text:
        with session_scope(session_factory) as session:
            lang = settings.get_language(session)
        await message.reply_text(i18n.t("guess.usage", lang))
        return

    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        game = game_service.active_or_setup_game(session)
        if game is None or game.status != GameStatus.ACTIVE:
            logger.warning("{} guessed with no ACTIVE game running", user.id)
            await message.reply_text(i18n.t("guess.no_game", lang))
            return
        if game.original_file_id is None or game.current_stage is None:
            # Shouldn't happen — an ACTIVE game always has both set. Defensive
            # guard (also narrows the type for the calls below).
            return
        if user.id == game.starter_id:
            logger.warning("Starter {} tried to guess on their own game {}", user.id, game.id)
            await message.reply_text(i18n.t("guess.starter_cannot_guess", lang))
            return

        players.get_or_create_player(session, user.id, username=user.username)
        logger.debug("{} guessed {!r} on game {}", user.id, guess_text, game.id)
        outcome = game_service.record_guess(
            session, game, guesser_id=user.id, guess_text=guess_text
        )

        if outcome is game_service.GuessOutcome.WON:
            timeout_module.cancel_timeout(context.job_queue, game.id)
            turn_state = game_service.get_turn_state(session)
            if turn_state is not None:
                timeout_module.schedule_turn_timers(context.job_queue, turn_state)
            await context.bot.send_photo(
                chat_id=group_chat_id,
                message_thread_id=game_topic_id,
                photo=game.original_file_id,
                caption=i18n.t(
                    "guess.won_caption",
                    lang,
                    winner=user.full_name,
                    title=game_service.display_title(game),
                ),
            )
            game_service.clear_original_screenshot(game)
        elif outcome is game_service.GuessOutcome.WRONG:
            stage, total_stages, remaining = game_service.stage_progress(session, game)
            await message.reply_text(
                i18n.t(
                    "guess.wrong_feedback",
                    lang,
                    remaining=remaining,
                    stage=stage,
                    total=total_stages,
                )
            )
        elif outcome is game_service.GuessOutcome.STAGE_ADVANCED:
            telegram_file = await context.bot.get_file(game.original_file_id)
            original_bytes = bytes(await telegram_file.download_as_bytearray())
            target_width = stage_config.get_stage_config(session)[game.current_stage].target_width
            pixelated = pixelate_service.pixelate(original_bytes, target_width)
            stage, total_stages, remaining = game_service.stage_progress(session, game)
            await context.bot.send_photo(
                chat_id=group_chat_id,
                message_thread_id=game_topic_id,
                photo=pixelated,
                caption=i18n.t(
                    "guess.stage_advanced_caption",
                    lang,
                    stage=stage,
                    total=total_stages,
                    guess_number=game.total_guess_count,
                    remaining=remaining,
                ),
            )
        elif outcome is game_service.GuessOutcome.UNSOLVED:
            await context.bot.send_photo(
                chat_id=group_chat_id,
                message_thread_id=game_topic_id,
                photo=game.original_file_id,
                caption=i18n.t(
                    "guess.unsolved_caption", lang, title=game_service.display_title(game)
                ),
            )
            game_service.clear_original_screenshot(game)
