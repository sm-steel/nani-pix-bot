"""Quiet-hours guard for timer callbacks — see MECHANICS.md's "Quiet
hours". Freshly computed deadlines already skip quiet windows
(services/game/clock.py); this is the safety net for deadlines computed
before quiet hours were set/changed, and for overdue deadlines re-armed
at startup (seconds_until() clamps those to fire immediately). The DB
deadline is left untouched — the job just re-schedules itself in memory,
and a restart mid-window re-arms it to fire immediately and land back
here."""

import functools
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from telegram.ext import ContextTypes

from nani_pix_bot.db import session_scope
from nani_pix_bot.services import quiet_hours, settings


def defer_if_quiet(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True if it's quiet now and the current job was re-scheduled for the
    end of the window — the caller must return without doing anything."""
    job = context.job
    job_queue = context.job_queue
    if job is None or job_queue is None:
        return False
    with session_scope(context.bot_data["session_factory"]) as session:
        qh = settings.get_quiet_hours(session)
    now = datetime.now(UTC)
    if qh is None or not quiet_hours.is_quiet(qh, now):
        return False
    resume_at = quiet_hours.window_end_after(qh, now)
    job_queue.run_once(job.callback, when=resume_at, name=job.name, data=job.data)
    logger.info("Quiet hours — deferred job {} until {}", job.name, resume_at.isoformat())
    return True


JobCallback = Callable[[ContextTypes.DEFAULT_TYPE], Coroutine[Any, Any, None]]


def quiet_hours_deferred(callback: JobCallback) -> JobCallback:
    """Decorates a timer callback so it runs defer_if_quiet() first — a
    decorator rather than a guard clause inside each callback so the
    callbacks' own control flow (several are already near qlty's
    complexity/returns limits) stays exactly as it was. The job is
    re-scheduled with the decorated callback itself (context.job.callback),
    so a deferred job is guarded again when it fires."""

    @functools.wraps(callback)
    async def wrapper(context: ContextTypes.DEFAULT_TYPE) -> None:
        if defer_if_quiet(context):
            return
        await callback(context)

    return wrapper
