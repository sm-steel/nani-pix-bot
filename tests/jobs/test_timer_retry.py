from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import OperationalError
from telegram.ext import ContextTypes

from nani_pix_bot.jobs import timers
from nani_pix_bot.jobs.timers.retry import (
    TIMER_MAX_RETRIES,
    TIMER_RETRY_DELAY,
    retry_on_failure,
)

TIMER_CALLBACKS = [
    timers.timeout_job_callback,
    timers.inactivity_nudge_job_callback,
    timers.inactivity_advance_job_callback,
    timers.setup_abandon_job_callback,
    timers.turn_reminder_job_callback,
    timers.turn_expiry_job_callback,
    timers.idle_autostart_job_callback,
]


def _context(callback, session_factory=None) -> MagicMock:
    context = MagicMock()
    context.bot_data = {"session_factory": session_factory}
    context.job.name = "some-job"
    context.job.data = 42
    context.job.callback = callback
    context.job_queue = MagicMock()
    return context


def _broken_session_factory():
    raise OperationalError("SELECT 1", {}, Exception("MySQL server has gone away"))


async def _run(callback, context: MagicMock) -> None:
    await callback(cast(ContextTypes.DEFAULT_TYPE, context))


@pytest.mark.parametrize("callback", TIMER_CALLBACKS, ids=lambda cb: cb.__name__)
async def test_timer_that_crashes_on_the_db_is_rescheduled_not_dropped(callback) -> None:
    """Issue #194: inactivity-advance hit a dead DB connection, raised, and
    — being a one-shot job — vanished, leaving the game stuck."""
    context = _context(callback, _broken_session_factory)

    with pytest.raises(OperationalError):
        await _run(callback, context)

    context.job_queue.run_once.assert_called_once_with(
        callback, when=TIMER_RETRY_DELAY, name="some-job", data=42
    )


async def test_gives_up_after_max_retries() -> None:
    failing = retry_on_failure(AsyncMock(side_effect=RuntimeError("boom")))
    context = _context(failing)

    for _ in range(TIMER_MAX_RETRIES + 1):
        with pytest.raises(RuntimeError):
            await _run(failing, context)

    assert context.job_queue.run_once.call_count == TIMER_MAX_RETRIES


async def test_retry_budget_resets_after_giving_up() -> None:
    """A later job reusing the same name (e.g. the next game's timer is
    re-armed) gets a fresh budget rather than inheriting an exhausted one."""
    failing = retry_on_failure(AsyncMock(side_effect=RuntimeError("boom")))
    context = _context(failing)
    for _ in range(TIMER_MAX_RETRIES + 1):
        with pytest.raises(RuntimeError):
            await _run(failing, context)
    context.job_queue.run_once.reset_mock()

    with pytest.raises(RuntimeError):
        await _run(failing, context)

    context.job_queue.run_once.assert_called_once()


async def test_success_resets_the_retry_budget() -> None:
    inner = AsyncMock(side_effect=[RuntimeError("boom"), None])
    wrapped = retry_on_failure(inner)
    context = _context(wrapped)

    with pytest.raises(RuntimeError):
        await _run(wrapped, context)
    await _run(wrapped, context)
    inner.side_effect = RuntimeError("boom")
    for _ in range(TIMER_MAX_RETRIES):
        with pytest.raises(RuntimeError):
            await _run(wrapped, context)

    assert context.job_queue.run_once.call_count == 1 + TIMER_MAX_RETRIES


async def test_success_does_not_reschedule() -> None:
    wrapped = retry_on_failure(AsyncMock(return_value=None))
    context = _context(wrapped)

    await _run(wrapped, context)

    context.job_queue.run_once.assert_not_called()
