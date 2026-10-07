from datetime import UTC, datetime
from typing import cast
from unittest.mock import MagicMock

from telegram.ext import ContextTypes

from nani_pix_bot.jobs import periods as period_jobs


async def test_the_boundary_job_rearms_itself_for_the_next_boundary(session_factory) -> None:
    context = MagicMock()
    context.job = None
    context.bot_data = {"session_factory": session_factory}
    context.job_queue.get_jobs_by_name.return_value = []

    await period_jobs.period_boundary_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    kwargs = context.job_queue.run_once.call_args.kwargs
    assert kwargs["name"] == period_jobs.PERIOD_JOB_NAME
    assert kwargs["when"] > datetime.now(UTC)
