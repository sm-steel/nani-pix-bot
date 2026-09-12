"""The /correct command — the author-override path when the fuzzy
matcher misses a genuinely correct guess. See MECHANICS.md's "Winning"
section."""

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.scoping import is_game_topic
from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import timers as timeout_module
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, players, settings


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
        game = await _validate_active_game_for_starter(session, message, user, lang)
        if game is None:
            return
        target = await _resolve_target_player(session, message, game, target_username, lang)
        if target is None:
            return
        # _validate_active_game_for_starter already checked this — restores
        # the type narrowing lost by returning `game` across a function
        # boundary.
        assert game.original_file_id is not None

        game_service.force_win(session, game, winner_id=target.telegram_user_id)
        logger.info(
            "Game {} force-won for {} by starter {} (/correct)",
            game.id,
            target.telegram_user_id,
            user.id,
        )
        timeout_module.cancel_timeout(context.job_queue, game.id)
        turn_state = game_service.get_turn_state(session)
        if turn_state is not None:
            timeout_module.schedule_turn_timers(context.job_queue, turn_state)
        await context.bot.send_photo(
            chat_id=group_chat_id,
            message_thread_id=game_topic_id,
            photo=game.original_file_id,
            caption=i18n.t(
                "correct.caption",
                lang,
                winner=target_username,
                title=game_service.display_title(game),
            ),
        )
        game_service.clear_original_screenshot(game)


async def _validate_active_game_for_starter(session, message, user, lang: str) -> Game | None:
    """Every check that must pass before /correct even looks for a target
    player: an ACTIVE game exists, the caller is its starter, and at
    least one guess has been made. Replies and returns None on the
    first failure."""
    game = game_service.active_or_setup_game(session)
    if game is None or game.status != GameStatus.ACTIVE:
        logger.warning("{} ran /correct with no ACTIVE game running", user.id)
        await message.reply_text(i18n.t("correct.no_game", lang))
        return None
    if game.starter_id != user.id:
        logger.warning("Non-starter {} tried /correct on game {}", user.id, game.id)
        await message.reply_text(i18n.t("correct.not_starter", lang))
        return None
    if game.total_guess_count == 0:
        logger.warning("{} tried /correct on game {} before any guess", user.id, game.id)
        await message.reply_text(i18n.t("correct.no_guesses_yet", lang))
        return None
    if game.original_file_id is None:
        return None  # shouldn't happen for an ACTIVE game — defensive guard
    return game


async def _resolve_target_player(
    session, message, game: Game, target_username: str, lang: str
) -> Player | None:
    target = players.find_player_by_username(session, target_username)
    if target is None:
        logger.warning("/correct: unknown username {!r} on game {}", target_username, game.id)
        await message.reply_text(i18n.t("correct.unknown_username", lang, username=target_username))
        return None
    return target
