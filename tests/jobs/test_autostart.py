from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.ext import ContextTypes

from nani_pix_bot.jobs.timers import autostart as autostart_timers
from nani_pix_bot.models.bot_settings import BotSettings
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.turn_state import TurnState
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services.game import autostart as autostart_service


def test_schedule_idle_autostart_calls_run_once_from_the_stored_deadline() -> None:
    job_queue = MagicMock()
    job_queue.get_jobs_by_name.return_value = []
    turn_state = TurnState(id=1, autostart_deadline_at=datetime.now(UTC) + timedelta(hours=24))

    autostart_timers.schedule_idle_autostart(job_queue, turn_state)

    job_queue.run_once.assert_called_once()
    _, kwargs = job_queue.run_once.call_args
    assert kwargs["name"] == autostart_timers.IDLE_AUTOSTART_JOB_NAME


def test_schedule_idle_autostart_is_a_noop_when_no_deadline_is_set() -> None:
    job_queue = MagicMock()
    job_queue.get_jobs_by_name.return_value = []
    turn_state = TurnState(id=1, autostart_deadline_at=None)

    autostart_timers.schedule_idle_autostart(job_queue, turn_state)

    job_queue.run_once.assert_not_called()


def test_cancel_idle_autostart_removes_the_named_job() -> None:
    job = MagicMock()
    job_queue = MagicMock()
    job_queue.get_jobs_by_name.return_value = [job]

    autostart_timers.cancel_idle_autostart(job_queue)

    job_queue.get_jobs_by_name.assert_called_once_with(autostart_timers.IDLE_AUTOSTART_JOB_NAME)
    job.schedule_removal.assert_called_once()


def _make_context(session_factory, *, bot_id: int = 999) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
        "search_client": MagicMock(),
        "tmdb_client": MagicMock(),
        "bot_username": "nani_pix_bot",
    }
    context.bot.id = bot_id
    context.bot.send_photo = AsyncMock(return_value=MagicMock(message_id=999))
    context.bot.pin_chat_message = AsyncMock()
    context.bot.unpin_chat_message = AsyncMock()
    context.job_queue = MagicMock()
    context.job_queue.get_jobs_by_name.return_value = []
    return context


async def test_idle_autostart_job_callback_noops_when_the_turn_is_no_longer_open(
    session_factory,
) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.add(TurnState(id=1, next_starter_id=1))
        session.commit()
    context = _make_context(session_factory)

    await autostart_timers.idle_autostart_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_photo.assert_not_awaited()


async def test_idle_autostart_job_callback_noops_when_a_game_is_already_running(
    session_factory,
) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.add(TurnState(id=1, next_starter_id=None))
        session.add(Game(starter_id=1, status=GameStatus.SETUP))
        session.commit()
    context = _make_context(session_factory)

    await autostart_timers.idle_autostart_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_photo.assert_not_awaited()


async def test_idle_autostart_job_callback_noops_when_disabled(session_factory) -> None:
    with session_factory() as session:
        session.add(TurnState(id=1, next_starter_id=None))
        session.add(BotSettings(id=1, games_enabled=True, autostart_enabled=False))
        session.commit()
    context = _make_context(session_factory)

    await autostart_timers.idle_autostart_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_photo.assert_not_awaited()


async def test_idle_autostart_job_callback_reschedules_on_a_failed_pick(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    with session_factory() as session:
        session.add(TurnState(id=1, next_starter_id=None))
        session.add(BotSettings(id=1, games_enabled=True, autostart_enabled=True))
        session.commit()
    context = _make_context(session_factory)

    async def failing_gather_pick(search_client, tmdb_client):
        return None

    monkeypatch.setattr(autostart_service, "gather_pick", failing_gather_pick)

    await autostart_timers.idle_autostart_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_photo.assert_not_awaited()
    context.job_queue.run_once.assert_called_once()
    with session_factory() as session:
        turn_state = game_service.get_turn_state(session)
        assert turn_state is not None
        assert turn_state.autostart_deadline_at is not None


async def test_maybe_overthrow_does_nothing_when_the_roll_misses(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.add(TurnState(id=1, next_starter_id=1))
        session.add(BotSettings(id=1, games_enabled=True, autostart_enabled=True))
        session.commit()
    context = _make_context(session_factory)
    monkeypatch.setattr(autostart_service, "roll_overthrow", lambda: False)

    await autostart_timers.maybe_overthrow(
        cast(ContextTypes.DEFAULT_TYPE, context),
        session_factory,
        winner_id=1,
        winner_name="frieren",
    )

    context.bot.send_photo.assert_not_awaited()


async def test_maybe_overthrow_claims_the_game_on_a_hit(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.add(TurnState(id=1, next_starter_id=1))
        session.add(BotSettings(id=1, games_enabled=True, autostart_enabled=True))
        session.commit()
    context = _make_context(session_factory)
    monkeypatch.setattr(autostart_service, "roll_overthrow", lambda: True)
    # A real pixelate() can't process fake screenshot bytes — same
    # monkeypatch tests/jobs/test_timers.py already uses for the same
    # reason.
    monkeypatch.setattr(
        "nani_pix_bot.services.pixelate.pixelate", lambda image_bytes, target_width: b"pixelated"
    )

    from nani_pix_bot.models.enums import Provider
    from nani_pix_bot.services.game.autostart import AnimePick, GatheredPick, ScreenshotPick
    from nani_pix_bot.services.search.shikimori import ShikimoriResult

    fake_pick = GatheredPick(
        anime=AnimePick(
            result=ShikimoriResult(1, "Frieren", None, None, []), source=Provider.SHIKIMORI
        ),
        screenshot=ScreenshotPick(provider=Provider.SHIKIMORI, provider_id=1, image_bytes=b"x"),
    )

    async def fake_gather_pick(search_client, tmdb_client):
        return fake_pick

    monkeypatch.setattr(autostart_service, "gather_pick", fake_gather_pick)

    await autostart_timers.maybe_overthrow(
        cast(ContextTypes.DEFAULT_TYPE, context),
        session_factory,
        winner_id=1,
        winner_name="frieren",
    )

    context.bot.send_photo.assert_awaited_once()
    _, kwargs = context.bot.send_photo.await_args
    assert "frieren" in kwargs["caption"]
    with session_factory() as session:
        turn_state = game_service.get_turn_state(session)
        assert turn_state is not None
        assert turn_state.next_starter_id is None
