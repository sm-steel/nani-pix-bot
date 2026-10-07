"""The period boundary job (spec §6): at every boundary, close whatever
ended — catching up after downtime — then re-arm for the next one. Started
at once from app.py's _post_init, which is also the downtime catch-up.
Posting is the outbox's job (quiet hours hold the posts, not the closing)."""

from datetime import UTC, datetime

from loguru import logger
from telegram.ext import ContextTypes, JobQueue

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs.timers._shared import job_log_scope
from nani_pix_bot.jobs.timers.retry import retry_on_failure
from nani_pix_bot.services import settings
from nani_pix_bot.services.achievements import periods

PERIOD_JOB_NAME = "period_boundary"


def schedule_period_job(job_queue: JobQueue | None, when: datetime | float) -> None:
    if job_queue is None:
        return
    for job in job_queue.get_jobs_by_name(PERIOD_JOB_NAME):
        job.schedule_removal()
    job_queue.run_once(period_boundary_callback, when=when, name=PERIOD_JOB_NAME)


@job_log_scope()
@retry_on_failure
async def period_boundary_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    with session_scope(context.bot_data["session_factory"]) as session:
        tz = settings.get_group_timezone(session)
        closed = periods.finalize_due(session, datetime.now(UTC), tz)
        next_at = periods.next_boundary(session)
    if not closed:
        logger.debug("period job ran — nothing had ended")
    if next_at is not None:
        schedule_period_job(context.job_queue, next_at)
        logger.debug("next period boundary at {next_at}", next_at=next_at.isoformat())
