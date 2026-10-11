from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import MagicMock

import pytest
from telegram.ext import ContextTypes

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import seasons as season_jobs
from nani_pix_bot.models.enums import SeasonStatus
from nani_pix_bot.models.season import SeasonSchedule


def _context(session_factory) -> MagicMock:
    context = MagicMock()
    context.job = None
    context.bot_data = {"session_factory": session_factory}
    context.job_queue.get_jobs_by_name.return_value = []
    return context


async def test_the_job_starts_a_due_season_and_rearms_for_its_end(session_factory) -> None:
    end = datetime.now(UTC) + timedelta(days=3)
    with session_scope(session_factory) as session:
        session.add(
            SeasonSchedule(
                run_id="demo_1",
                start_at=datetime.now(UTC) - timedelta(minutes=1),
                end_at=end,
                status=SeasonStatus.SCHEDULED,
                created_by=9,
            )
        )
    context = _context(session_factory)
    await season_jobs.season_boundary_callback(cast(ContextTypes.DEFAULT_TYPE, context))
    with session_scope(session_factory) as session:
        assert session.query(SeasonSchedule).one().status == SeasonStatus.ACTIVE
    kwargs = context.job_queue.run_once.call_args.kwargs
    assert kwargs["name"] == season_jobs.SEASON_JOB_NAME
    assert abs((kwargs["when"] - end).total_seconds()) < 1


async def test_with_no_season_it_checks_back_later(session_factory) -> None:
    context = _context(session_factory)
    await season_jobs.season_boundary_callback(cast(ContextTypes.DEFAULT_TYPE, context))
    assert context.job_queue.run_once.call_args.kwargs["when"] > datetime.now(UTC)


async def test_a_failing_run_still_rearms(session_factory, monkeypatch) -> None:
    def broken(*_a):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(season_jobs.lifecycle, "advance", broken)
    context = _context(session_factory)
    context.job = MagicMock()
    context.job.name = season_jobs.SEASON_JOB_NAME
    with pytest.raises(RuntimeError):
        await season_jobs.season_boundary_callback(cast(ContextTypes.DEFAULT_TYPE, context))
    names = [c.kwargs["name"] for c in context.job_queue.run_once.call_args_list]
    assert season_jobs.SEASON_JOB_NAME in names


async def test_a_closing_season_with_no_running_games_is_ended_by_one_run(session_factory) -> None:
    # R6: the hourly fallback finalizes a season whose last game was deleted.
    with session_scope(session_factory) as session:
        session.add(
            SeasonSchedule(
                run_id="demo_1",
                start_at=datetime.now(UTC) - timedelta(days=3),
                end_at=datetime.now(UTC) - timedelta(minutes=1),
                status=SeasonStatus.CLOSING,
                created_by=9,
            )
        )
    context = _context(session_factory)
    await season_jobs.season_boundary_callback(cast(ContextTypes.DEFAULT_TYPE, context))
    with session_scope(session_factory) as session:
        row = session.query(SeasonSchedule).one()
        assert row.status == SeasonStatus.ENDED
        assert row.ended_at is not None
