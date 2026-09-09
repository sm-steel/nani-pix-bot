"""2-day timeout JobQueue wiring — see MECHANICS.md's "Timeout" section.

JobQueue jobs don't survive a process restart, so `rearm_pending_timeouts`
is called from app.py on startup to re-schedule any still-ACTIVE game's
timeout from its stored `scheduled_end_at` — a redeploy never silently
loses or resets the clock.
"""

from telegram.ext import ContextTypes, JobQueue

from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import game as game_service


def _title(game: Game) -> str:
    return game.title_english or game.title_romaji or game.title_native or "?"


def schedule_timeout(job_queue: JobQueue | None, game: Game) -> None:
    if job_queue is None:
        return
    job_queue.run_once(
        timeout_job_callback,
        when=game_service.seconds_until_timeout(game),
        name=game_service.timeout_job_name(game.id),
        data=game.id,
    )


def cancel_timeout(job_queue: JobQueue | None, game_id: int) -> None:
    if job_queue is None:
        return
    for job in job_queue.get_jobs_by_name(game_service.timeout_job_name(game_id)):
        job.schedule_removal()


async def timeout_job_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    if job is None:
        return
    game_id = job.data

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        game = session.get(Game, game_id)
        if game is None or game.status != GameStatus.ACTIVE or game.original_file_id is None:
            return

        game_service.force_unsolved(game)
        await context.bot.send_photo(
            chat_id=context.bot_data["group_chat_id"],
            message_thread_id=context.bot_data["game_topic_id"],
            photo=game.original_file_id,
            caption=f"⏰ Nobody guessed it in time. It was {_title(game)}.",
        )
        game_service.clear_original_screenshot(game)


async def rearm_pending_timeouts(job_queue: JobQueue | None, session_factory) -> None:
    with session_scope(session_factory) as session:
        for game in game_service.active_games(session):
            schedule_timeout(job_queue, game)
