from datetime import UTC, datetime
from typing import cast
from unittest.mock import MagicMock

import pytest
from telegram.ext import ContextTypes

from nani_pix_bot.jobs import periods as period_jobs
from nani_pix_bot.jobs.timers.retry import TIMER_MAX_RETRIES, TIMER_RETRY_DELAY


async def test_the_boundary_job_rearms_itself_for_the_next_boundary(session_factory) -> None:
    context = MagicMock()
    context.job = None
    context.bot_data = {"session_factory": session_factory}
    context.job_queue.get_jobs_by_name.return_value = []

    await period_jobs.period_boundary_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    kwargs = context.job_queue.run_once.call_args.kwargs
    assert kwargs["name"] == period_jobs.PERIOD_JOB_NAME
    assert kwargs["when"] > datetime.now(UTC)


def _context(session_factory) -> MagicMock:
    context = MagicMock()
    context.bot_data = {"session_factory": session_factory}
    context.job_queue.get_jobs_by_name.return_value = []
    return context


async def test_a_job_that_gives_up_retrying_still_rearms(session_factory, monkeypatch) -> None:
    def broken(*_args: object) -> list:
        msg = "db hiccup"
        raise RuntimeError(msg)

    monkeypatch.setattr(period_jobs.periods, "finalize_due", broken)
    context = _context(session_factory)
    context.job.name = period_jobs.PERIOD_JOB_NAME
    context.bot_data["timer_retries"] = {period_jobs.PERIOD_JOB_NAME: TIMER_MAX_RETRIES}

    with pytest.raises(RuntimeError):
        await period_jobs.period_boundary_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    (call,) = context.job_queue.run_once.call_args_list  # no retry left: only the re-arm
    assert call.kwargs["name"] == period_jobs.PERIOD_JOB_NAME
    assert call.kwargs["when"] > datetime.now(UTC)


async def test_a_failing_run_keeps_its_retry_and_rearms(session_factory, monkeypatch) -> None:
    def broken(*_args: object) -> list:
        msg = "db hiccup"
        raise RuntimeError(msg)

    monkeypatch.setattr(period_jobs.periods, "finalize_due", broken)
    context = _context(session_factory)
    context.job.name = period_jobs.PERIOD_JOB_NAME

    with pytest.raises(RuntimeError):
        await period_jobs.period_boundary_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    whens = [c.kwargs["when"] for c in context.job_queue.run_once.call_args_list]
    assert TIMER_RETRY_DELAY in whens  # the retry decorator still retries in a minute
    assert any(isinstance(w, datetime) and w > datetime.now(UTC) for w in whens)
