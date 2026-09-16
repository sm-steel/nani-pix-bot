"""The 2-day absolute game timeout — see MECHANICS.md's "Timeout"
section."""

from typing import cast

from loguru import logger
from telegram.ext import ContextTypes, JobQueue

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs.timers._shared import seconds_until_timeout
from nani_pix_bot.jobs.timers.current_image import clear_image_if_sent, post_current_image
from nani_pix_bot.jobs.timers.inactivity import cancel_inactivity_timers
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, settings


def timeout_job_name(game_id: int) -> str:
    """Deterministic JobQueue job name for a game's timeout — lets this
    module look up and cancel a pending job (e.g. on a win) or re-arm it
    on startup without storing anything extra on the row."""
    return f"game-timeout-{game_id}"


def schedule_timeout(job_queue: JobQueue | None, game: Game) -> None:
    if job_queue is None:
        return
    delay = seconds_until_timeout(game)
    logger.debug("Scheduling 2-day timeout for game {} in {:.0f}s", game.id, delay)
    job_queue.run_once(
        timeout_job_callback,
        when=delay,
        name=timeout_job_name(game.id),
        data=game.id,
    )


def cancel_timeout(job_queue: JobQueue | None, game_id: int) -> None:
    if job_queue is None:
        return
    logger.debug("Canceling 2-day timeout for game {}", game_id)
    for job in job_queue.get_jobs_by_name(timeout_job_name(game_id)):
        job.schedule_removal()


async def timeout_job_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    if job is None:
        return
    game_id = cast(int, job.data)

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        game = session.get(Game, game_id)
        if game is None or game.status != GameStatus.ACTIVE or game.original_image is None:
            logger.debug("Timeout fired for game {} but it's already resolved — no-op", game_id)
            return

        logger.info("Game {} timed out after 2 days — ending unsolved", game_id)
        game_service.force_unsolved(game)
        cancel_inactivity_timers(context.job_queue, game.id)
        original_bytes = game.original_image
        caption = i18n.t("timeout.caption", lang, title=game_service.display_title(game, lang))
    # Block closed and committed above — the UNSOLVED ending is durable
    # now regardless of whether the announcement below actually reaches
    # the group (see post_current_image's docstring).
    sent = await post_current_image(context, session_factory, photo=original_bytes, caption=caption)
    clear_image_if_sent(session_factory, game_id, sent)
