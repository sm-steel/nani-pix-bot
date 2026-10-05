"""Retry guard for timer callbacks. Every timer is a one-shot run_once
job: if its callback raises, the job is simply gone, and nothing re-arms
it until the next restart (rearm_pending_timeouts) — issue #194, where a
transient DB failure left a game stuck at its last stage. This re-schedules
the failed job a bounded number of times instead, then re-raises so the
application's error handler still logs the full traceback."""

import functools
from datetime import timedelta

from loguru import logger
from telegram.ext import ContextTypes

from nani_pix_bot.jobs.timers.quiet import JobCallback

TIMER_RETRY_DELAY = timedelta(minutes=1)
TIMER_MAX_RETRIES = 3

_RETRIES_KEY = "timer_retries"


def _schedule_retry(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    job_queue = context.job_queue
    if job is None or job_queue is None:
        return
    retries: dict[str, int] = context.bot_data.setdefault(_RETRIES_KEY, {})
    attempt = retries.get(job.name or "", 0) + 1
    if attempt > TIMER_MAX_RETRIES:
        retries.pop(job.name or "", None)
        logger.error("timer job failed {retries} retries — giving up", retries=TIMER_MAX_RETRIES)
        return
    retries[job.name or ""] = attempt
    job_queue.run_once(job.callback, when=TIMER_RETRY_DELAY, name=job.name, data=job.data)
    logger.error(
        "timer job failed — retry {attempt}/{retries} in {delay}",
        attempt=attempt,
        retries=TIMER_MAX_RETRIES,
        delay=TIMER_RETRY_DELAY,
    )


def retry_on_failure(callback: JobCallback) -> JobCallback:
    """Decorates a timer callback (outermost, so it also covers the
    quiet-hours guard's own DB read). The job is re-scheduled with
    context.job.callback, i.e. this decorated wrapper, so a retry is
    guarded again."""

    @functools.wraps(callback)
    async def wrapper(context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            await callback(context)
        except Exception:
            _schedule_retry(context)
            raise
        if context.job is not None:
            context.bot_data.get(_RETRIES_KEY, {}).pop(context.job.name or "", None)

    return wrapper
