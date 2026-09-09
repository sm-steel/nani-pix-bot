from typing import cast
from unittest.mock import AsyncMock, MagicMock

from telegram.ext import ContextTypes

from nani_pix_bot.commands import timeout as timeout_module
from nani_pix_bot.models.enums import GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import game as game_service


def _active_game(session_factory, **overrides) -> int:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.commit()
        defaults = {
            "starter_id": 1,
            "original_file_id": "file123",
            "status": GameStatus.ACTIVE,
            "current_stage": PixelStage.X10,
            "title_english": "Frieren: Beyond Journey's End",
        }
        defaults.update(overrides)
        game = Game(**defaults)
        session.add(game)
        session.commit()
        return game.id


def test_schedule_timeout_calls_run_once_with_the_games_job_name(session_factory) -> None:
    game_id = _active_game(session_factory)
    job_queue = MagicMock()
    with session_factory() as session:
        game = session.get(Game, game_id)
        timeout_module.schedule_timeout(job_queue, game)

    job_queue.run_once.assert_called_once()
    _, kwargs = job_queue.run_once.call_args
    assert kwargs["name"] == game_service.timeout_job_name(game_id)
    assert kwargs["data"] == game_id


def test_cancel_timeout_removes_matching_jobs() -> None:
    job = MagicMock()
    job_queue = MagicMock()
    job_queue.get_jobs_by_name.return_value = [job]

    timeout_module.cancel_timeout(job_queue, 42)

    job_queue.get_jobs_by_name.assert_called_once_with(game_service.timeout_job_name(42))
    job.schedule_removal.assert_called_once()


def _make_job_context(session_factory, *, game_id: int) -> MagicMock:
    context = MagicMock()
    context.job.data = game_id
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    context.bot.send_photo = AsyncMock()
    return context


async def test_timeout_job_callback_ends_the_game_unsolved(session_factory) -> None:
    game_id = _active_game(session_factory)
    context = _make_job_context(session_factory, game_id=game_id)

    await timeout_module.timeout_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_photo.assert_awaited_once()
    _, kwargs = context.bot.send_photo.await_args
    assert kwargs["photo"] == "file123"

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.status == GameStatus.UNSOLVED
        assert fetched.original_file_id is None


async def test_timeout_job_callback_is_a_noop_if_already_won(session_factory) -> None:
    game_id = _active_game(session_factory, status=GameStatus.WON, winner_id=1)
    context = _make_job_context(session_factory, game_id=game_id)

    await timeout_module.timeout_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_photo.assert_not_awaited()


async def test_rearm_pending_timeouts_schedules_every_active_game(session_factory) -> None:
    game_id = _active_game(session_factory)
    job_queue = MagicMock()

    await timeout_module.rearm_pending_timeouts(job_queue, session_factory)

    job_queue.run_once.assert_called_once()
    _, kwargs = job_queue.run_once.call_args
    assert kwargs["name"] == game_service.timeout_job_name(game_id)


def test_schedule_timeout_is_a_noop_when_job_queue_is_none(session_factory) -> None:
    game_id = _active_game(session_factory)
    with session_factory() as session:
        game = session.get(Game, game_id)
        timeout_module.schedule_timeout(None, game)  # should not raise


def test_cancel_timeout_is_a_noop_when_job_queue_is_none() -> None:
    timeout_module.cancel_timeout(None, 42)  # should not raise


async def test_rearm_pending_timeouts_is_a_noop_when_job_queue_is_none(session_factory) -> None:
    _active_game(session_factory)

    await timeout_module.rearm_pending_timeouts(None, session_factory)  # should not raise
