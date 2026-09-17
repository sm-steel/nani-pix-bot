"""The 3h-nudge/6h-auto-advance inactivity timers — see MECHANICS.md's
"Inactivity" section."""

from typing import cast

from loguru import logger
from telegram.ext import ContextTypes, JobQueue

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs.timers._shared import seconds_until
from nani_pix_bot.jobs.timers.current_image import clear_image_if_sent, post_current_image
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, settings
from nani_pix_bot.services import pixelate as pixelate_service
from nani_pix_bot.services.settings import stage_config


def inactivity_nudge_job_name(game_id: int) -> str:
    """Deterministic JobQueue job name for a game's inactivity nudge —
    mirrors game_timeout.timeout_job_name()."""
    return f"inactivity-nudge-{game_id}"


def inactivity_advance_job_name(game_id: int) -> str:
    """Deterministic JobQueue job name for a game's inactivity
    auto-advance — mirrors game_timeout.timeout_job_name()."""
    return f"inactivity-advance-{game_id}"


def schedule_inactivity_timers(job_queue: JobQueue | None, game: Game) -> None:
    """(Re)schedules both per-game jobs from `game.inactivity_nudge_at`/
    `inactivity_advance_at` — cancels any existing ones first (same
    guard turn_timers.schedule_turn_timers uses), so resetting the clock
    on every guess or auto-advance never double-schedules."""
    if job_queue is None:
        return
    cancel_inactivity_timers(job_queue, game.id)
    if game.inactivity_nudge_at is not None:
        delay = seconds_until(game.inactivity_nudge_at)
        logger.debug("Scheduling inactivity-nudge for game {} in {:.0f}s", game.id, delay)
        job_queue.run_once(
            inactivity_nudge_job_callback,
            when=delay,
            name=inactivity_nudge_job_name(game.id),
            data=game.id,
        )
    if game.inactivity_advance_at is not None:
        delay = seconds_until(game.inactivity_advance_at)
        logger.debug("Scheduling inactivity-advance for game {} in {:.0f}s", game.id, delay)
        job_queue.run_once(
            inactivity_advance_job_callback,
            when=delay,
            name=inactivity_advance_job_name(game.id),
            data=game.id,
        )


def cancel_inactivity_timers(job_queue: JobQueue | None, game_id: int) -> None:
    if job_queue is None:
        return
    logger.debug("Canceling inactivity nudge/advance timers for game {}", game_id)
    for name in (inactivity_nudge_job_name(game_id), inactivity_advance_job_name(game_id)):
        for job in job_queue.get_jobs_by_name(name):
            job.schedule_removal()


async def inactivity_nudge_job_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fires INACTIVITY_NUDGE_DELAY after the last guess (or activation)
    on an ACTIVE game. Points at whatever's currently pinned so the
    nudge visually references the last posted image, without revealing
    the title.

    Clears inactivity_nudge_at after a successful send — a one-shot
    reminder, not a repeating one (see clear_inactivity_nudge's
    docstring): leaving the deadline stuck in the past would otherwise
    make rearm_pending_timeouts re-derive and re-fire this same nudge on
    every subsequent redeploy, since JobQueue jobs never survive a
    process restart. Session stays open across the send (unlike
    inactivity_advance_job_callback below) since a plain text send has
    none of post_current_image's proxy-timeout risk, and the clear
    commits only if the send didn't raise."""
    job = context.job
    if job is None:
        return
    game_id = job.data

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        game = session.get(Game, game_id)
        if game is None or game.status != GameStatus.ACTIVE:
            logger.debug("Inactivity nudge fired for game {} but it's not ACTIVE — no-op", game_id)
            return
        pinned_message_id = settings.get_pinned_message_id(session)

        logger.info("Game {} nudged after inactivity", game_id)
        await context.bot.send_message(
            chat_id=context.bot_data["group_chat_id"],
            message_thread_id=context.bot_data["game_topic_id"],
            text=i18n.t("guess.inactivity_nudge", lang),
            reply_to_message_id=pinned_message_id,
        )
        game_service.clear_inactivity_nudge(game)


async def inactivity_advance_job_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fires INACTIVITY_ADVANCE_DELAY after the last guess (or
    activation) on an ACTIVE game, with no guess in between to reset the
    clock. Advances the stage exactly like a guess-driven exhaustion
    would (via the shared advance_stage()), or ends the game unsolved
    if it was already on the last stage.

    Imports maybe_overthrow lazily (function-local, not at module level):
    autostart.py already imports schedule_inactivity_timers from this
    module, so a module-level import the other way round would be a real
    circular import, not just the theoretical shape the sibling-import
    convention is meant to dodge — Python's import system can't resolve
    two modules that both need each other fully initialized at parse
    time. See turn_timers.py's turn_expiry_job_callback and
    game_timeout.py's timeout_job_callback for the identical situation."""
    from nani_pix_bot.jobs.timers.autostart import maybe_overthrow

    job = context.job
    if job is None:
        return
    game_id = cast(int, job.data)

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        game = session.get(Game, game_id)
        if game is None or game.status != GameStatus.ACTIVE or game.current_stage is None:
            logger.debug(
                "Inactivity advance fired for game {} but it's not ACTIVE — no-op", game_id
            )
            return
        if game.original_image is None:
            # Genuine internal invariant, not a type-narrowing artifact: an
            # ACTIVE game always has its bytes (see state.py's
            # has_answer_to_reveal docstring), and unlike guess.py's /
            # correct's handlers, nothing earlier in this background job
            # callback loaded or checked original_image — there is no
            # synchronous caller here to otherwise notice a silent failure.
            msg = f"Game {game_id}: inactivity-advance fired but original_image is missing"
            logger.error(msg)
            raise RuntimeError(msg)

        outcome = game_service.advance_stage(game)
        if outcome is game_service.GuessOutcome.UNSOLVED:
            game_service.mark_turn_open_if_unassigned(session)

        if outcome is game_service.GuessOutcome.UNSOLVED:
            logger.info("Game {} auto-ended unsolved after repeated inactivity", game_id)
            original_bytes = game.original_image
            unsolved_caption = i18n.t(
                "guess.unsolved_caption", lang, title=game_service.display_title(game, lang)
            )
        else:
            logger.info(
                "Game {} auto-advanced to stage {} after inactivity", game_id, game.current_stage
            )
            target_width = stage_config.get_stage_config(session, game.current_stage).target_width
            pixelated = pixelate_service.pixelate(game.original_image, target_width)
            progress = game_service.stage_progress(session, game)
            advanced_caption = i18n.t(
                "guess.inactivity_advanced_caption",
                lang,
                stage=progress.number,
                total=progress.total,
            )
            game_service.reset_inactivity_clock(game)
            schedule_inactivity_timers(context.job_queue, game)
    # Block closed and committed above — the stage advance (or UNSOLVED
    # ending) is durable now regardless of whether the announcement below
    # actually reaches the group (see post_current_image's docstring).
    if outcome is game_service.GuessOutcome.UNSOLVED:
        sent = await post_current_image(
            context, session_factory, photo=original_bytes, caption=unsolved_caption
        )
        clear_image_if_sent(session_factory, game_id, sent)
        await maybe_overthrow(context, session_factory)
        return

    await post_current_image(context, session_factory, photo=pixelated, caption=advanced_caption)
