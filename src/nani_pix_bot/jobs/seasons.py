"""The season boundary job (seasons spec §1): at a season's start or end,
advance it (catching up after downtime), then re-arm for the next
boundary. Started at once from app.py's _post_init, and re-run at once by
/season after every change. With nothing on the clock it checks back
hourly. Posts are the outbox's job (quiet hours hold posts, not the clock)."""

from datetime import UTC, datetime, timedelta

from loguru import logger
from telegram.ext import ContextTypes, JobQueue

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs.timers._shared import job_log_scope
from nani_pix_bot.jobs.timers.retry import retry_on_failure
from nani_pix_bot.services.seasons import lifecycle

SEASON_JOB_NAME = "season_boundary"
# When the next boundary is unknown or already overdue (advancing failed).
REARM_FALLBACK = timedelta(hours=1)


def schedule_season_job(job_queue: JobQueue | None, when: datetime | float) -> None:
    if job_queue is None:
        return
    for job in job_queue.get_jobs_by_name(SEASON_JOB_NAME):
        job.schedule_removal()
    job_queue.run_once(season_boundary_callback, when=when, name=SEASON_JOB_NAME)


def _advance(context: ContextTypes.DEFAULT_TYPE) -> None:
    with session_scope(context.bot_data["session_factory"]) as session:
        moved = lifecycle.advance(session, datetime.now(UTC))
    if not moved:
        logger.debug("season job ran — nothing was due")


def _next_run(context: ContextTypes.DEFAULT_TYPE) -> datetime:
    """The next boundary; or, when that can't be read or is already due
    (advancing just failed), a fallback later run that catches up then —
    never a past time, which would re-run at once in a tight loop."""
    fallback = datetime.now(UTC) + REARM_FALLBACK
    try:
        with session_scope(context.bot_data["session_factory"]) as session:
            next_at = lifecycle.next_boundary(session)
    except Exception:
        logger.opt(exception=True).error("couldn't read the next season boundary")
        return fallback
    if next_at is None or next_at <= datetime.now(UTC):
        return fallback
    return next_at


def _rearm(context: ContextTypes.DEFAULT_TYPE) -> None:
    next_at = _next_run(context)
    schedule_season_job(context.job_queue, next_at)
    logger.debug("next season boundary run at {next_at}", next_at=next_at.isoformat())


@job_log_scope()
@retry_on_failure
async def season_boundary_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Re-arms whatever happens: once the retry decorator gives up, the
    re-armed run is what still advances the season."""
    try:
        _advance(context)
    finally:
        _rearm(context)
