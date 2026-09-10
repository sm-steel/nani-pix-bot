from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from telegram.error import Forbidden
from telegram.ext import ContextTypes

from nani_pix_bot.commands import timeout as timeout_module
from nani_pix_bot.models.enums import GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.turn_state import TurnState
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


def _setup_game(session_factory, **overrides) -> int:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.commit()
        defaults = {
            "starter_id": 1,
            "original_file_id": "file123",
            "status": GameStatus.SETUP,
            "setup_deadline": datetime.now(UTC) + timedelta(hours=1),
        }
        defaults.update(overrides)
        game = Game(**defaults)
        session.add(game)
        session.commit()
        return game.id


def test_schedule_setup_abandon_calls_run_once_with_the_games_job_name(session_factory) -> None:
    game_id = _setup_game(session_factory)
    job_queue = MagicMock()
    with session_factory() as session:
        game = session.get(Game, game_id)
        timeout_module.schedule_setup_abandon(job_queue, game)

    job_queue.run_once.assert_called_once()
    _, kwargs = job_queue.run_once.call_args
    assert kwargs["name"] == game_service.setup_abandon_job_name(game_id)
    assert kwargs["data"] == game_id


def test_cancel_setup_abandon_removes_matching_jobs() -> None:
    job = MagicMock()
    job_queue = MagicMock()
    job_queue.get_jobs_by_name.return_value = [job]

    timeout_module.cancel_setup_abandon(job_queue, 42)

    job_queue.get_jobs_by_name.assert_called_once_with(game_service.setup_abandon_job_name(42))
    job.schedule_removal.assert_called_once()


def _make_group_job_context(session_factory) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    context.bot.send_message = AsyncMock()
    context.job_queue = MagicMock()
    context.job_queue.get_jobs_by_name.return_value = []
    return context


async def test_setup_abandon_job_callback_deletes_the_row_and_opens_the_turn(
    session_factory,
) -> None:
    game_id = _setup_game(session_factory)
    context = _make_group_job_context(session_factory)
    context.job.data = game_id

    await timeout_module.setup_abandon_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_message.assert_awaited_once()
    _, kwargs = context.bot.send_message.await_args
    assert kwargs["chat_id"] == 555
    with session_factory() as session:
        assert session.get(Game, game_id) is None
        turn_state = game_service.get_turn_state(session)
        assert turn_state is None or turn_state.next_starter_id is None


async def test_setup_abandon_job_callback_is_a_noop_if_already_confirmed(
    session_factory,
) -> None:
    game_id = _setup_game(session_factory, status=GameStatus.ACTIVE, current_stage=PixelStage.X10)
    context = _make_group_job_context(session_factory)
    context.job.data = game_id

    await timeout_module.setup_abandon_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_message.assert_not_awaited()
    with session_factory() as session:
        assert session.get(Game, game_id) is not None


def test_schedule_turn_timers_schedules_both_jobs(session_factory) -> None:
    job_queue = MagicMock()
    job_queue.get_jobs_by_name.return_value = []
    turn_state = TurnState(
        id=1,
        next_starter_id=2,
        reminder_at=datetime.now(UTC) + timedelta(minutes=15),
        expiry_at=datetime.now(UTC) + timedelta(hours=12),
    )

    timeout_module.schedule_turn_timers(job_queue, turn_state)

    names = [call.kwargs["name"] for call in job_queue.run_once.call_args_list]
    assert timeout_module.TURN_REMINDER_JOB_NAME in names
    assert timeout_module.TURN_EXPIRY_JOB_NAME in names


def test_schedule_turn_timers_schedules_nothing_when_the_turn_is_open() -> None:
    job_queue = MagicMock()
    job_queue.get_jobs_by_name.return_value = []
    turn_state = TurnState(id=1, next_starter_id=None)

    timeout_module.schedule_turn_timers(job_queue, turn_state)

    job_queue.run_once.assert_not_called()


def test_cancel_turn_timers_removes_both_named_jobs() -> None:
    job = MagicMock()
    job_queue = MagicMock()
    job_queue.get_jobs_by_name.return_value = [job]

    timeout_module.cancel_turn_timers(job_queue)

    assert job_queue.get_jobs_by_name.call_count == 2
    assert job.schedule_removal.call_count == 2


async def test_turn_reminder_job_callback_dms_the_designated_player(session_factory) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=2, username="frieren"))
        session.add(TurnState(id=1, next_starter_id=2))
        session.commit()

    context = _make_group_job_context(session_factory)

    await timeout_module.turn_reminder_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_message.assert_awaited_once()
    _, kwargs = context.bot.send_message.await_args
    assert kwargs["chat_id"] == 2


async def test_turn_reminder_job_callback_falls_back_to_group_mention_when_dm_fails(
    session_factory,
) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=2, username="frieren"))
        session.add(TurnState(id=1, next_starter_id=2))
        session.commit()

    context = _make_group_job_context(session_factory)
    context.bot.send_message = AsyncMock(side_effect=[Forbidden("bot was blocked"), None])

    await timeout_module.turn_reminder_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    assert context.bot.send_message.await_count == 2
    assert context.bot.send_message.await_args is not None
    _, kwargs = context.bot.send_message.await_args
    assert kwargs["chat_id"] == 555
    assert "frieren" in kwargs["text"]


async def test_turn_reminder_job_callback_is_a_noop_if_a_game_is_already_running(
    session_factory,
) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=2))
        session.add(TurnState(id=1, next_starter_id=2))
        session.add(Game(starter_id=2, original_file_id="f", status=GameStatus.SETUP))
        session.commit()

    context = _make_group_job_context(session_factory)

    await timeout_module.turn_reminder_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_message.assert_not_awaited()


async def test_turn_expiry_job_callback_opens_the_turn_and_notifies_the_group(
    session_factory,
) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=2))
        session.add(TurnState(id=1, next_starter_id=2))
        session.commit()

    context = _make_group_job_context(session_factory)

    await timeout_module.turn_expiry_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_message.assert_awaited_once()
    _, kwargs = context.bot.send_message.await_args
    assert kwargs["chat_id"] == 555
    with session_factory() as session:
        turn_state = game_service.get_turn_state(session)
        assert turn_state is not None
        assert turn_state.next_starter_id is None


async def test_rearm_pending_timeouts_reschedules_setup_abandon_and_turn_timers(
    session_factory,
) -> None:
    _setup_game(session_factory)
    with session_factory() as session:
        session.add(Player(telegram_user_id=2))
        session.add(
            TurnState(
                id=1,
                next_starter_id=2,
                reminder_at=datetime.now(UTC) + timedelta(minutes=15),
                expiry_at=datetime.now(UTC) + timedelta(hours=12),
            )
        )
        session.commit()
    job_queue = MagicMock()
    job_queue.get_jobs_by_name.return_value = []

    await timeout_module.rearm_pending_timeouts(job_queue, session_factory)

    names = [call.kwargs["name"] for call in job_queue.run_once.call_args_list]
    assert timeout_module.TURN_REMINDER_JOB_NAME in names
    assert timeout_module.TURN_EXPIRY_JOB_NAME in names
    assert any(name.startswith("setup-abandon-") for name in names)
