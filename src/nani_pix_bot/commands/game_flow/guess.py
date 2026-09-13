"""The /guess command — see MECHANICS.md's "Guess matching" and
"Pixelation stages" sections."""

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.scoping import is_game_topic
from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import timers as timeout_module
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game
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
        game = await _validate_guess(session, message, user, lang)
        if game is None:
            return
        # _validate_guess already checked this is set — restores the type
        # narrowing lost by returning `game` across a function boundary.
        # original_image is deliberately NOT checked/asserted here: it's a
        # deferred column (see models/game.py), and a WRONG guess — by far
        # the most common outcome — never reads it. Asserting it up front
        # would force-load the blob on every single /guess. Each branch
        # below that actually needs the bytes asserts it locally instead.
        assert game.current_stage is not None

        players.get_or_create_player(session, user.id, username=user.username)
        logger.debug("{} guessed {!r} on game {}", user.id, guess_text, game.id)
        outcome = game_service.record_guess(
            session, game, guesser_id=user.id, guess_text=guess_text
        )

        if outcome is game_service.GuessOutcome.WON:
            assert game.original_image is not None
            timeout_module.cancel_timeout(context.job_queue, game.id)
            timeout_module.cancel_inactivity_timers(context.job_queue, game.id)
            turn_state = game_service.get_turn_state(session)
            if turn_state is not None:
                timeout_module.schedule_turn_timers(context.job_queue, turn_state)
            await timeout_module.post_current_image(
                context,
                session,
                photo=game.original_image,
                caption=i18n.t(
                    "guess.won_caption",
                    lang,
                    winner=user.full_name,
                    title=game_service.display_title(game, lang),
                ),
            )
            game_service.clear_original_screenshot(game)
        elif outcome is game_service.GuessOutcome.WRONG:
            stage, total_stages, remaining = game_service.stage_progress(session, game)
            game_service.reset_inactivity_clock(game)
            timeout_module.schedule_inactivity_timers(context.job_queue, game)
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
            assert game.original_image is not None
            original_bytes = game.original_image
            target_width = stage_config.get_stage_config(session)[game.current_stage].target_width
            pixelated = pixelate_service.pixelate(original_bytes, target_width)
            stage, total_stages, remaining = game_service.stage_progress(session, game)
            game_service.reset_inactivity_clock(game)
            timeout_module.schedule_inactivity_timers(context.job_queue, game)
            await timeout_module.post_current_image(
                context,
                session,
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
            assert game.original_image is not None
            timeout_module.cancel_inactivity_timers(context.job_queue, game.id)
            await timeout_module.post_current_image(
                context,
                session,
                photo=game.original_image,
                caption=i18n.t(
                    "guess.unsolved_caption", lang, title=game_service.display_title(game, lang)
                ),
            )
            game_service.clear_original_screenshot(game)


async def _validate_guess(session, message, user, lang: str) -> Game | None:
    """The game must be ACTIVE (with current_stage set, which an ACTIVE
    game always has), and the starter may not guess on their own round.
    Replies and returns None on the first failure.

    Deliberately does NOT check original_image here — it's a deferred
    column (see models/game.py), and current_stage being set is already
    the same "this is a properly-activated ACTIVE game" invariant
    without touching it. Loading original_image on every /guess (most of
    which are a WRONG outcome that never reads it) would defeat the
    point of deferring it in the first place."""
    game = game_service.active_or_setup_game(session)
    if game is None or game.status != GameStatus.ACTIVE:
        logger.warning("{} guessed with no ACTIVE game running", user.id)
        await message.reply_text(i18n.t("guess.no_game", lang))
        return None
    if game.current_stage is None:
        # Shouldn't happen — an ACTIVE game always has this set. Defensive guard.
        return None
    if user.id == game.starter_id:
        logger.warning("Starter {} tried to guess on their own game {}", user.id, game.id)
        await message.reply_text(i18n.t("guess.starter_cannot_guess", lang))
        return None
    return game
