"""The period boundary job (spec §6): at every boundary, close whatever
ended — catching up after downtime — then re-arm for the next one. Started
at once from app.py's _post_init, which is also the downtime catch-up —
and only runs because app.py tells the JobQueue to run jobs however late
they are due (#336: armed before the scheduler starts, it used to be
dropped as a misfire on a slow start, and nothing else re-arms it).
Posting is the outbox's job (quiet hours hold the posts, not the closing)."""

from datetime import UTC, datetime, timedelta

from loguru import logger
from telegram.ext import ContextTypes, JobQueue

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs.timers._shared import job_log_scope
from nani_pix_bot.jobs.timers.retry import retry_on_failure
from nani_pix_bot.services import settings
from nani_pix_bot.services.achievements import periods

PERIOD_JOB_NAME = "period_boundary"
# When the next boundary is unknown or already overdue (closing failed).
REARM_FALLBACK = timedelta(hours=1)


def schedule_period_job(job_queue: JobQueue | None, when: datetime | float) -> None:
    if job_queue is None:
        return
    for job in job_queue.get_jobs_by_name(PERIOD_JOB_NAME):
        job.schedule_removal()
    job_queue.run_once(period_boundary_callback, when=when, name=PERIOD_JOB_NAME)


def _close_due(context: ContextTypes.DEFAULT_TYPE) -> None:
    with session_scope(context.bot_data["session_factory"]) as session:
        tz = settings.get_group_timezone(session)
        closed = periods.finalize_due(session, datetime.now(UTC), tz)
    if not closed:
        logger.debug("period job ran — nothing had ended")


def _next_run(context: ContextTypes.DEFAULT_TYPE) -> datetime:
    """The next boundary; or, when that can't be read or is already due
    (closing just failed), a fallback later run that catches up then —
    never a past time, which would re-run at once in a tight loop."""
    fallback = datetime.now(UTC) + REARM_FALLBACK
    try:
        with session_scope(context.bot_data["session_factory"]) as session:
            next_at = periods.next_boundary(session)
    except Exception:
        logger.opt(exception=True).error("couldn't read the next period boundary")
        return fallback
    if next_at is None or next_at <= datetime.now(UTC):
        return fallback
    return next_at


def _rearm(context: ContextTypes.DEFAULT_TYPE) -> None:
    next_at = _next_run(context)
    schedule_period_job(context.job_queue, next_at)
    logger.debug("next period boundary run at {next_at}", next_at=next_at.isoformat())


@job_log_scope()
@retry_on_failure
async def period_boundary_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Re-arms whatever happens: once the retry decorator gives up, the
    re-armed run is what still closes the period."""
    try:
        _close_due(context)
    finally:
        _rearm(context)
