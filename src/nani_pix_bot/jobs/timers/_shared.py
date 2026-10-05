"""Scheduling primitives shared by every timer submodule in this
package — datetime math and the per-job log scope, no DB access of its
own."""

import functools
from collections.abc import Callable
from datetime import UTC, datetime

from telegram.ext import ContextTypes

from nani_pix_bot import log_context
from nani_pix_bot.jobs.timers.quiet import JobCallback
from nani_pix_bot.models.game import Game


def job_log_scope(data_key: str | None = None) -> Callable[[JobCallback], JobCallback]:
    """Starts a timer job's structured log context (issue #230, see
    log_context.py): the job's name, plus `job.data` under `data_key`
    (`game_id`, `player_id`) when the job carries one. Applied outermost,
    above retry_on_failure, so a retried job (re-scheduled with this same
    wrapper as its callback) gets its scope too.

    It *resets*: a job task inherits the context of whatever scheduled it
    — usually the update that started the game — and that player is not
    who this job's lines are about."""

    def decorate(callback: JobCallback) -> JobCallback:
        @functools.wraps(callback)
        async def wrapper(context: ContextTypes.DEFAULT_TYPE) -> None:
            job = context.job
            fields: dict[str, object] = {}
            if job is not None:
                fields["job"] = job.name
                if data_key is not None and job.data is not None:
                    fields[data_key] = job.data
            log_context.reset(**fields)
            await callback(context)

        return wrapper

    return decorate


def seconds_until(deadline: datetime | None) -> float:
    """Seconds from now until `deadline`, clamped at 0 for an already-
    overdue deadline (or if there's no deadline at all). Normalizes naive
    datetimes (as DATETIME columns round-trip from the DB) to UTC before
    comparing — shared by the game timeout, setup-abandon, turn, and
    inactivity timers."""
    if deadline is None:
        return 0.0
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    return max((deadline - datetime.now(UTC)).total_seconds(), 0.0)


def seconds_until_timeout(game: Game) -> float:
    """Seconds from now until `game.scheduled_end_at` — see seconds_until()."""
    return seconds_until(game.scheduled_end_at)
