from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes

from nani_pix_bot.commands import season as season_module
from nani_pix_bot.jobs.seasons import SEASON_JOB_NAME
from nani_pix_bot.models.enums import SeasonStatus
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.services import i18n, players
from nani_pix_bot.services.seasons import schedule
from nani_pix_bot.services.seasons.schedule import ScheduleRequest

ADMIN = 1
MOSCOW = "Europe/Moscow"


@pytest.fixture(autouse=True)
def fake_runs(monkeypatch):
    runs = registry.discover("tests.seasons.fake_runs")
    monkeypatch.setattr(registry, "all_runs", lambda: runs)
    return runs


def _context(session_factory, *, admin: bool = True, args: list[str] | None = None) -> MagicMock:
    context = MagicMock()
    context.bot_data = {"session_factory": session_factory, "group_chat_id": 555}
    status = ChatMemberStatus.ADMINISTRATOR if admin else ChatMemberStatus.MEMBER
    context.bot.get_chat_member = AsyncMock(return_value=MagicMock(status=status))
    context.args = args or []
    context.job_queue.get_jobs_by_name.return_value = []
    return context


def _update(*, user_id: int = ADMIN) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.type = "private"
    update.message.reply_text = AsyncMock()
    return update


def _reply_text(update: MagicMock) -> str:
    args, kwargs = update.message.reply_text.await_args
    return args[0] if args else kwargs["text"]


async def _run(update: MagicMock, context: MagicMock) -> None:
    await season_module.season_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )


def _set_timezone(session_factory, name: str = MOSCOW) -> None:
    with session_factory() as session:
        players.set_timezone(session, ADMIN, ZoneInfo(name))
        session.commit()


def _rows(session_factory) -> list[SeasonSchedule]:
    with session_factory() as session:
        return list(session.scalars(select(SeasonSchedule)))


def _request(now: datetime) -> ScheduleRequest:
    return ScheduleRequest("demo_1", now + timedelta(days=1), now + timedelta(days=5), ADMIN)


async def test_schedule_without_timezone_asks_for_one(session_factory) -> None:
    update = _update()
    args = ["schedule", "demo_1", "2030-01-10", "18:00", "2030-01-20", "18:00"]
    await _run(update, _context(session_factory, args=args))
    assert _reply_text(update) == i18n.t("season.need_timezone", "EN")
    assert _rows(session_factory) == []


async def test_schedule_creates_the_season_and_rearms_the_job(session_factory) -> None:
    _set_timezone(session_factory)
    update = _update()
    args = ["schedule", "demo_1", "2030-01-10", "18:00", "2030-01-20", "18:00"]
    context = _context(session_factory, args=args)
    await _run(update, context)
    (row,) = _rows(session_factory)
    assert row.status == SeasonStatus.SCHEDULED
    assert row.start_at.replace(tzinfo=UTC) == datetime(2030, 1, 10, 15, 0, tzinfo=UTC)
    assert row.created_by == ADMIN
    context.job_queue.run_once.assert_called_once()
    assert context.job_queue.run_once.call_args.kwargs["name"] == SEASON_JOB_NAME
    assert context.job_queue.run_once.call_args.kwargs["when"] == 0
    assert "demo_1" in _reply_text(update)
    assert "2030-01-10 18:00" in _reply_text(update)


async def test_refusals_are_explained(session_factory) -> None:
    _set_timezone(session_factory)
    update = _update()
    args = ["schedule", "nope", "2030-01-10", "18:00", "2030-01-20", "18:00"]
    context = _context(session_factory, args=args)
    await _run(update, context)
    assert _reply_text(update) == i18n.t("season.refused.unknown_run", "EN")
    context.job_queue.run_once.assert_not_called()


async def test_unparseable_dates_show_usage(session_factory) -> None:
    _set_timezone(session_factory)
    update = _update()
    args = ["schedule", "demo_1", "10.01.2030", "18:00", "2030-01-20", "18:00"]
    await _run(update, _context(session_factory, args=args))
    assert _reply_text(update) == i18n.t("season.usage", "EN")


async def test_end_now_ends_a_running_season(session_factory) -> None:
    _set_timezone(session_factory)
    now = datetime.now(UTC)
    with session_factory() as session:
        row = schedule.schedule(session, _request(now), now)
        row.status, row.started_at = SeasonStatus.ACTIVE, now
        session.commit()
    await _run(_update(), _context(session_factory, args=["end", "now"]))
    (stored,) = _rows(session_factory)
    assert abs(stored.end_at.replace(tzinfo=UTC) - now) < timedelta(minutes=1)


async def test_cancel_cancels_a_scheduled_season(session_factory) -> None:
    _set_timezone(session_factory)
    now = datetime.now(UTC)
    with session_factory() as session:
        schedule.schedule(session, _request(now), now)
        session.commit()
    update = _update()
    await _run(update, _context(session_factory, args=["cancel"]))
    (stored,) = _rows(session_factory)
    assert stored.status == SeasonStatus.CANCELLED
    assert "demo_1" in _reply_text(update)


async def test_non_admin_is_refused(session_factory) -> None:
    update = _update()
    await _run(update, _context(session_factory, admin=False, args=["cancel"]))
    assert _reply_text(update) == i18n.t("commands.admins_only", "EN")


async def test_bare_season_lists_available_runs(session_factory) -> None:
    _set_timezone(session_factory)
    update = _update()
    await _run(update, _context(session_factory))
    text = _reply_text(update)
    assert "demo_1" in text
    assert "Demo Season" in text


async def test_status_shows_who_scheduled_the_current_season(session_factory) -> None:
    _set_timezone(session_factory)
    now = datetime.now(UTC)
    with session_factory() as session:
        players.get_or_create_player(session, ADMIN, username="boss")
        schedule.schedule(session, _request(now), now)
        session.commit()
    update = _update()
    await _run(update, _context(session_factory))
    assert "@boss" in _reply_text(update)


async def test_status_history_shows_who_scheduled_it_too(session_factory) -> None:
    _set_timezone(session_factory)
    now = datetime.now(UTC)
    with session_factory() as session:
        players.get_or_create_player(session, ADMIN, username="boss")
        schedule.schedule(session, _request(now), now)
        schedule.cancel(session, now)
        session.commit()
    update = _update()
    await _run(update, _context(session_factory))
    text = _reply_text(update)
    assert i18n.t("season.status.history", "EN") in text
    assert "@boss" in text
