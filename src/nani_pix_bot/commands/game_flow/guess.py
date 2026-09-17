"""The /guess command — see MECHANICS.md's "Guess matching" and
"Pixelation stages" sections."""

from dataclasses import dataclass

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


@dataclass(frozen=True)
class _Announcement:
    """What a WON/STAGE_ADVANCED/UNSOLVED outcome needs to post once its
    session has committed — captured as plain values (not the ORM
    object) since the announcement happens after that session closes."""

    photo: bytes
    caption: str


def _prepare_won_announcement(
    session, context: ContextTypes.DEFAULT_TYPE, game: Game, lang: str, winner_name: str
) -> _Announcement:
    original_bytes = _require_original_image(game, "a WON outcome")
    timeout_module.cancel_timeout(context.job_queue, game.id)
    timeout_module.cancel_inactivity_timers(context.job_queue, game.id)
    turn_state = game_service.get_turn_state(session)
    if turn_state is not None:
        timeout_module.schedule_turn_timers(context.job_queue, turn_state)
    caption = i18n.t(
        "guess.won_caption", lang, winner=winner_name, title=game_service.display_title(game, lang)
    )
    return _Announcement(photo=original_bytes, caption=caption)


def _prepare_stage_advanced_announcement(
    session, context: ContextTypes.DEFAULT_TYPE, game: Game, lang: str
) -> _Announcement:
    # guess_command already checked this is set — restores the type
    # narrowing lost by passing `game` across a function boundary, same
    # as _require_original_image does for original_image below.
    if game.current_stage is None:
        raise RuntimeError("game.current_stage is None on a STAGE_ADVANCED outcome")
    original_bytes = _require_original_image(game, "a STAGE_ADVANCED outcome")
    target_width = stage_config.get_stage_config(session, game.current_stage).target_width
    pixelated = pixelate_service.pixelate(original_bytes, target_width, game.pixel_algorithm)
    progress = game_service.stage_progress(session, game)
    game_service.reset_inactivity_clock(game)
    timeout_module.schedule_inactivity_timers(context.job_queue, game)
    caption = i18n.t(
        "guess.stage_advanced_caption",
        lang,
        stage=progress.number,
        total=progress.total,
        remaining=progress.remaining,
        limit=progress.limit,
    )
    return _Announcement(photo=pixelated, caption=caption)


def _prepare_unsolved_announcement(
    context: ContextTypes.DEFAULT_TYPE, game: Game, lang: str
) -> _Announcement:
    original_bytes = _require_original_image(game, "an UNSOLVED outcome")
    timeout_module.cancel_inactivity_timers(context.job_queue, game.id)
    caption = i18n.t("guess.unsolved_caption", lang, title=game_service.display_title(game, lang))
    return _Announcement(photo=original_bytes, caption=caption)


def _require_original_image(game: Game, situation: str) -> bytes:
    """Restores the type narrowing lost across guess_command's WON/
    STAGE_ADVANCED/UNSOLVED branches for original_image, converting the
    old bare `assert` at each site to a real exception (S101, issue
    #117) so it can't silently vanish under `python -O`. Hoisted out of
    guess_command into its own function (rather than an inline
    `if ... raise` at each of the three call sites) purely to keep
    guess_command's own cyclomatic complexity under qlty's threshold —
    see CLAUDE.md's Tooling section.

    original_image is deliberately NOT checked/loaded any earlier than
    this: it's a deferred column (see models/game.py), and a WRONG
    guess — by far the most common outcome — never reads it. Checking
    it up front would force-load the blob on every single /guess; each
    branch that actually needs the bytes calls this once, right where
    it's used."""
    if game.original_image is None:
        raise RuntimeError(f"game.original_image is None on {situation}")
    return game.original_image


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

    announcement: _Announcement | None = None
    needs_cleanup_after_send = False
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        game = await _validate_guess(session, message, user, lang)
        if game is None:
            return
        # _validate_guess already checked this is set — restores the type
        # narrowing lost by returning `game` across a function boundary.
        if game.current_stage is None:
            raise RuntimeError("game.current_stage is None despite _validate_guess's check")

        players.get_or_create_player(session, user.id, username=user.username)
        logger.debug("{} guessed {!r} on game {}", user.id, guess_text, game.id)
        outcome = game_service.record_guess(
            session, game, guesser_id=user.id, guess_text=guess_text
        )
        game_id = game.id

        if outcome is game_service.GuessOutcome.WON:
            announcement = _prepare_won_announcement(session, context, game, lang, user.full_name)
            needs_cleanup_after_send = True
        elif outcome is game_service.GuessOutcome.WRONG:
            progress = game_service.stage_progress(session, game)
            game_service.reset_inactivity_clock(game)
            timeout_module.schedule_inactivity_timers(context.job_queue, game)
            await message.reply_text(
                i18n.t(
                    "guess.wrong_feedback",
                    lang,
                    remaining=progress.remaining,
                    limit=progress.limit,
                    stage=progress.number,
                    total=progress.total,
                )
            )
        elif outcome is game_service.GuessOutcome.STAGE_ADVANCED:
            announcement = _prepare_stage_advanced_announcement(session, context, game, lang)
        elif outcome is game_service.GuessOutcome.UNSOLVED:
            announcement = _prepare_unsolved_announcement(context, game, lang)
            needs_cleanup_after_send = True
    # Block closed and committed above — the outcome is durable now
    # regardless of whether the announcement below actually reaches the
    # group (see post_current_image's docstring).
    if announcement is not None:
        sent = await timeout_module.post_current_image(
            context, session_factory, photo=announcement.photo, caption=announcement.caption
        )
        if needs_cleanup_after_send:
            timeout_module.clear_image_if_sent(session_factory, game_id, sent)


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
