from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.ext import ContextTypes

from nani_pix_bot.jobs import timers
from nani_pix_bot.jobs.timers.quiet import defer_if_quiet
from nani_pix_bot.services import quiet_hours, settings
from nani_pix_bot.services.quiet_hours import QuietHours

GUARDED_CALLBACKS = [
    timers.timeout_job_callback,
    timers.inactivity_nudge_job_callback,
    timers.inactivity_advance_job_callback,
    timers.setup_abandon_job_callback,
    timers.turn_reminder_job_callback,
    timers.turn_expiry_job_callback,
    timers.idle_autostart_job_callback,
]


def _context(session_factory, callback) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    context.job.name = "some-job"
    context.job.data = 42
    context.job.callback = callback
    context.job_queue = MagicMock()
    context.bot.send_message = AsyncMock()
    context.bot.send_photo = AsyncMock()
    context.bot.send_media_group = AsyncMock()
    return context


def _enable_quiet(session_factory, qh: QuietHours) -> None:
    with session_factory() as session:
        settings.set_quiet_hours(session, qh)
        session.commit()


@pytest.mark.parametrize("callback", GUARDED_CALLBACKS, ids=lambda cb: cb.__name__)
async def test_guarded_callback_defers_to_window_end_and_posts_nothing(
    session_factory, quiet_now: QuietHours, callback
) -> None:
    _enable_quiet(session_factory, quiet_now)
    context = _context(session_factory, callback)

    await callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.job_queue.run_once.assert_called_once()
    args, kwargs = context.job_queue.run_once.call_args
    assert args[0] is callback
    assert kwargs["name"] == "some-job"
    assert kwargs["data"] == 42
    expected = quiet_hours.window_end_after(quiet_now, datetime.now(UTC))
    assert abs((kwargs["when"] - expected).total_seconds()) < 5
    context.bot.send_message.assert_not_awaited()
    context.bot.send_photo.assert_not_awaited()
    context.bot.send_media_group.assert_not_awaited()


def test_defer_if_quiet_is_false_without_quiet_hours(session_factory) -> None:
    context = _context(session_factory, timers.timeout_job_callback)
    assert defer_if_quiet(cast(ContextTypes.DEFAULT_TYPE, context)) is False
    context.job_queue.run_once.assert_not_called()


def test_defer_if_quiet_is_false_without_a_job(session_factory, quiet_now: QuietHours) -> None:
    _enable_quiet(session_factory, quiet_now)
    context = _context(session_factory, timers.timeout_job_callback)
    context.job = None
    assert defer_if_quiet(cast(ContextTypes.DEFAULT_TYPE, context)) is False
