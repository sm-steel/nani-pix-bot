"""The 1-hour setup-abandon timer — see MECHANICS.md's "Starting a
game" section."""

from loguru import logger
from telegram.ext import ContextTypes, JobQueue

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs.timers._shared import seconds_until
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, settings


def setup_abandon_job_name(game_id: int) -> str:
    """Deterministic JobQueue job name for a SETUP game's abandon
    timer — mirrors game_timeout.timeout_job_name()."""
    return f"setup-abandon-{game_id}"


def schedule_setup_abandon(job_queue: JobQueue | None, game: Game) -> None:
    if job_queue is None:
        return
    delay = seconds_until(game.setup_deadline)
    logger.debug("Scheduling setup-abandon for game {} in {:.0f}s", game.id, delay)
    job_queue.run_once(
        setup_abandon_job_callback,
        when=delay,
        name=setup_abandon_job_name(game.id),
        data=game.id,
    )


def cancel_setup_abandon(job_queue: JobQueue | None, game_id: int) -> None:
    if job_queue is None:
        return
    logger.debug("Canceling setup-abandon timer for game {}", game_id)
    for job in job_queue.get_jobs_by_name(setup_abandon_job_name(game_id)):
        job.schedule_removal()


async def setup_abandon_job_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fires 1h after a SETUP game is created. If the starter never
    confirmed (still SETUP), deletes the orphaned row and opens the turn
    — structurally forecloses the class of bug issue #11 fixed
    reactively (a SETUP row blocking the whole group indefinitely)."""
    job = context.job
    if job is None:
        return
    game_id = job.data

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        game = session.get(Game, game_id)
        if game is None or game.status != GameStatus.SETUP:
            logger.debug("Setup-abandon fired for game {} but it's already resolved", game_id)
            return
        logger.info("Game {} setup abandoned after 1h — deleting and opening the turn", game_id)
        session.delete(game)
        game_service.set_next_starter(session, None)

    await context.bot.send_message(
        chat_id=context.bot_data["group_chat_id"],
        message_thread_id=context.bot_data["game_topic_id"],
        text=i18n.t("dm_start.setup_abandoned", lang),
    )
