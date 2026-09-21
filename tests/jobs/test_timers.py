from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import BadRequest, Forbidden, TimedOut
from telegram.ext import ContextTypes

from nani_pix_bot.jobs import timers as timeout_module
from nani_pix_bot.models.enums import GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.turn_state import TurnState
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import settings


def _active_game(session_factory, **overrides) -> int:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.commit()
        defaults = {
            "starter_id": 1,
            "original_image": b"file123",
            "status": GameStatus.ACTIVE,
            "current_stage": PixelStage.STAGE_1,
            "title_english": "Frieren: Beyond Journey's End",
        }
        defaults.update(overrides)
        game = Game(**defaults)
        session.add(game)
        session.commit()
        return game.id


def _hard_mode_active_game(session_factory, **overrides) -> int:
    """An ACTIVE hard-mode game — current_stage/original_image are
    always None for these (Task 4: every autostart game is hard mode
    and never populates original_image), which is exactly the
    combination the old guards in inactivity.py/game_timeout.py used to
    silently no-op on forever."""
    defaults = {
        "current_stage": None,
        "original_image": None,
        "hard_mode": True,
        "hard_mode_turn": 1,
        "hard_mode_image_a": b"hm-image-a",
        "hard_mode_image_b": b"hm-image-b",
    }
    defaults.update(overrides)
    return _active_game(session_factory, **defaults)


def test_schedule_timeout_calls_run_once_with_the_games_job_name(session_factory) -> None:
    game_id = _active_game(session_factory)
    job_queue = MagicMock()
    with session_factory() as session:
        game = session.get(Game, game_id)
        timeout_module.schedule_timeout(job_queue, game)

    job_queue.run_once.assert_called_once()
    _, kwargs = job_queue.run_once.call_args
    assert kwargs["name"] == timeout_module.timeout_job_name(game_id)
    assert kwargs["data"] == game_id


def test_cancel_timeout_removes_matching_jobs() -> None:
    job = MagicMock()
    job_queue = MagicMock()
    job_queue.get_jobs_by_name.return_value = [job]

    timeout_module.cancel_timeout(job_queue, 42)

    job_queue.get_jobs_by_name.assert_called_once_with(timeout_module.timeout_job_name(42))
    job.schedule_removal.assert_called_once()


def _make_job_context(session_factory, *, game_id: int) -> MagicMock:
    context = MagicMock()
    context.job.data = game_id
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    context.bot.send_photo = AsyncMock(return_value=MagicMock(message_id=999))
    context.bot.send_media_group = AsyncMock(
        return_value=[MagicMock(message_id=998), MagicMock(message_id=999)]
    )
    context.bot.pin_chat_message = AsyncMock()
    context.bot.unpin_chat_message = AsyncMock()
    context.job_queue = MagicMock()
    context.job_queue.get_jobs_by_name.return_value = []
    return context


async def test_timeout_job_callback_ends_the_game_unsolved(session_factory) -> None:
    game_id = _active_game(session_factory)
    context = _make_job_context(session_factory, game_id=game_id)

    await timeout_module.timeout_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_photo.assert_awaited_once()
    _, kwargs = context.bot.send_photo.await_args
    assert kwargs["photo"] == b"file123"

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.status == GameStatus.UNSOLVED
        assert fetched.original_image is None


async def test_timeout_job_callback_rolls_overthrow(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_id = _active_game(session_factory)
    context = _make_job_context(session_factory, game_id=game_id)
    calls = []

    async def fake_maybe_overthrow(context, session_factory, **kwargs):
        calls.append(kwargs)

    # Patched on autostart.py, not game_timeout.py: timeout_job_callback
    # imports maybe_overthrow lazily (function-local) to dodge a real
    # circular import (see the docstring on timeout_job_callback), so
    # game_timeout.py never has a module-level `maybe_overthrow` name to
    # patch — the lazy import re-reads autostart.maybe_overthrow fresh on
    # every call, which is exactly what makes it patchable here.
    monkeypatch.setattr("nani_pix_bot.jobs.timers.autostart.maybe_overthrow", fake_maybe_overthrow)

    await timeout_module.timeout_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    assert len(calls) == 1
    assert calls[0].get("winner_id") is None


async def test_timeout_job_callback_is_a_noop_if_already_won(session_factory) -> None:
    game_id = _active_game(session_factory, status=GameStatus.WON, winner_id=1)
    context = _make_job_context(session_factory, game_id=game_id)

    await timeout_module.timeout_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_photo.assert_not_awaited()


async def test_timeout_job_callback_keeps_the_game_unsolved_when_the_reveal_times_out(
    session_factory,
) -> None:
    game_id = _active_game(session_factory)
    context = _make_job_context(session_factory, game_id=game_id)
    context.bot.send_photo = AsyncMock(side_effect=TimedOut())

    await timeout_module.timeout_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.status == GameStatus.UNSOLVED
        assert fetched.original_image == b"file123"


async def test_timeout_job_callback_acts_on_hard_mode_game_with_no_original_image(
    session_factory,
) -> None:
    """Regression test for the second load-bearing bug this task fixes:
    a hard-mode game's original_image is always None (it uses
    hard_mode_image_a/_b instead), and the old guard
    (`game.original_image is None`) silently no-opped every hard-mode
    game forever — never timing it out. This must actually act, not
    no-op."""
    game_id = _hard_mode_active_game(session_factory)
    context = _make_job_context(session_factory, game_id=game_id)

    await timeout_module.timeout_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_media_group.assert_awaited_once()
    context.bot.send_photo.assert_not_awaited()
    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.status == GameStatus.UNSOLVED


async def test_timeout_job_callback_ends_hard_mode_game_unsolved_via_two_photo_reveal(
    session_factory,
) -> None:
    game_id = _hard_mode_active_game(session_factory)
    context = _make_job_context(session_factory, game_id=game_id)

    await timeout_module.timeout_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    _, kwargs = context.bot.send_media_group.call_args
    media = kwargs["media"]
    assert len(media) == 2
    assert media[0].media.input_file_content == b"hm-image-a"
    assert media[1].media.input_file_content == b"hm-image-b"
    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.hard_mode_image_a is None
        assert fetched.hard_mode_image_b is None


async def test_timeout_job_callback_rolls_overthrow_for_hard_mode_game(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_id = _hard_mode_active_game(session_factory)
    context = _make_job_context(session_factory, game_id=game_id)
    calls = []

    async def fake_maybe_overthrow(context, session_factory, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("nani_pix_bot.jobs.timers.autostart.maybe_overthrow", fake_maybe_overthrow)

    await timeout_module.timeout_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    assert len(calls) == 1
    assert calls[0].get("winner_id") is None


async def test_timeout_job_callback_is_a_noop_for_normal_game_with_no_original_image(
    session_factory,
) -> None:
    """The normal-mode (non-hard-mode) no-op behavior for a missing
    original_image must be unaffected by the hard-mode guard change."""
    game_id = _active_game(session_factory, original_image=None)
    context = _make_job_context(session_factory, game_id=game_id)

    await timeout_module.timeout_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_photo.assert_not_awaited()
    context.bot.send_media_group.assert_not_awaited()


async def test_rearm_pending_timeouts_schedules_every_active_game(session_factory) -> None:
    game_id = _active_game(session_factory)
    job_queue = MagicMock()

    await timeout_module.rearm_pending_timeouts(job_queue, session_factory)

    job_queue.run_once.assert_called_once()
    _, kwargs = job_queue.run_once.call_args
    assert kwargs["name"] == timeout_module.timeout_job_name(game_id)


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
            "original_image": b"file123",
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
    assert kwargs["name"] == timeout_module.setup_abandon_job_name(game_id)
    assert kwargs["data"] == game_id


def test_cancel_setup_abandon_removes_matching_jobs() -> None:
    job = MagicMock()
    job_queue = MagicMock()
    job_queue.get_jobs_by_name.return_value = [job]

    timeout_module.cancel_setup_abandon(job_queue, 42)

    job_queue.get_jobs_by_name.assert_called_once_with(timeout_module.setup_abandon_job_name(42))
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
    game_id = _setup_game(
        session_factory, status=GameStatus.ACTIVE, current_stage=PixelStage.STAGE_1
    )
    context = _make_group_job_context(session_factory)
    context.job.data = game_id

    await timeout_module.setup_abandon_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_message.assert_not_awaited()
    with session_factory() as session:
        assert session.get(Game, game_id) is not None


async def test_setup_abandon_job_callback_schedules_idle_autostart(session_factory) -> None:
    game_id = _setup_game(session_factory)
    context = _make_group_job_context(session_factory)
    context.job.data = game_id

    await timeout_module.setup_abandon_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    names = [call.kwargs["name"] for call in context.job_queue.run_once.call_args_list]
    assert timeout_module.IDLE_AUTOSTART_JOB_NAME in names


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
    # Every job is targeted at the specific player it was scheduled for,
    # so a stale-target job (superseded by a later retarget) can no-op
    # instead of acting against the wrong player.
    data_values = [call.kwargs["data"] for call in job_queue.run_once.call_args_list]
    assert data_values == [2, 2]


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
    context.job.data = 2

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
    context.job.data = 2
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
        session.add(Game(starter_id=2, original_image=b"f", status=GameStatus.SETUP))
        session.commit()

    context = _make_group_job_context(session_factory)
    context.job.data = 2

    await timeout_module.turn_reminder_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_message.assert_not_awaited()


async def test_turn_reminder_job_callback_is_a_noop_if_targeted_at_a_stale_player(
    session_factory,
) -> None:
    """A reminder scheduled for player 2, superseded by a later win/skip
    that retargeted the turn to player 3 before this job fired, must not
    DM player 2 — see schedule_turn_timers' data= hardening."""
    with session_factory() as session:
        session.add_all([Player(telegram_user_id=2), Player(telegram_user_id=3)])
        session.add(TurnState(id=1, next_starter_id=3))
        session.commit()

    context = _make_group_job_context(session_factory)
    context.job.data = 2

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
    context.job.data = 2

    await timeout_module.turn_expiry_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_message.assert_awaited_once()
    _, kwargs = context.bot.send_message.await_args
    assert kwargs["chat_id"] == 555
    with session_factory() as session:
        turn_state = game_service.get_turn_state(session)
        assert turn_state is not None
        assert turn_state.next_starter_id is None


async def test_turn_expiry_job_callback_is_a_noop_if_targeted_at_a_stale_player(
    session_factory,
) -> None:
    """An expiry scheduled for player 2, superseded by a later win/skip
    that retargeted the turn to player 3 before this job fired, must not
    open player 3's turn back up — see schedule_turn_timers' data=
    hardening."""
    with session_factory() as session:
        session.add_all([Player(telegram_user_id=2), Player(telegram_user_id=3)])
        session.add(TurnState(id=1, next_starter_id=3))
        session.commit()

    context = _make_group_job_context(session_factory)
    context.job.data = 2

    await timeout_module.turn_expiry_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_message.assert_not_awaited()
    with session_factory() as session:
        turn_state = game_service.get_turn_state(session)
        assert turn_state is not None
        assert turn_state.next_starter_id == 3


async def test_turn_expiry_job_callback_schedules_idle_autostart(session_factory) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=2))
        session.add(TurnState(id=1, next_starter_id=2))
        session.commit()

    context = _make_group_job_context(session_factory)
    context.job.data = 2

    await timeout_module.turn_expiry_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    names = [call.kwargs["name"] for call in context.job_queue.run_once.call_args_list]
    assert timeout_module.IDLE_AUTOSTART_JOB_NAME in names


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


def test_timeout_job_name_is_stable_and_unique_per_game() -> None:
    assert timeout_module.timeout_job_name(42) == timeout_module.timeout_job_name(42)
    assert timeout_module.timeout_job_name(42) != timeout_module.timeout_job_name(43)


def test_setup_abandon_job_name_is_stable_and_unique_per_game() -> None:
    assert timeout_module.setup_abandon_job_name(42) == timeout_module.setup_abandon_job_name(42)
    assert timeout_module.setup_abandon_job_name(42) != timeout_module.setup_abandon_job_name(43)


def test_seconds_until_timeout_handles_aware_datetimes() -> None:
    game = Game(starter_id=1, original_image=b"f")
    game.scheduled_end_at = datetime.now(UTC) + timedelta(seconds=100)

    assert timeout_module.seconds_until_timeout(game) == pytest.approx(100, abs=1)


def test_seconds_until_timeout_treats_naive_datetimes_as_utc() -> None:
    game = Game(starter_id=1, original_image=b"f")
    game.scheduled_end_at = (datetime.now(UTC) + timedelta(seconds=100)).replace(tzinfo=None)

    assert timeout_module.seconds_until_timeout(game) == pytest.approx(100, abs=1)


def test_seconds_until_timeout_clamps_overdue_to_zero() -> None:
    game = Game(starter_id=1, original_image=b"f")
    game.scheduled_end_at = datetime.now(UTC) - timedelta(days=1)

    assert timeout_module.seconds_until_timeout(game) == 0


def test_seconds_until_handles_aware_datetimes() -> None:
    deadline = datetime.now(UTC) + timedelta(seconds=100)

    assert timeout_module.seconds_until(deadline) == pytest.approx(100, abs=1)


def test_seconds_until_treats_naive_datetimes_as_utc() -> None:
    deadline = (datetime.now(UTC) + timedelta(seconds=100)).replace(tzinfo=None)

    assert timeout_module.seconds_until(deadline) == pytest.approx(100, abs=1)


def test_seconds_until_clamps_overdue_to_zero() -> None:
    deadline = datetime.now(UTC) - timedelta(days=1)

    assert timeout_module.seconds_until(deadline) == 0


def test_seconds_until_returns_zero_for_none() -> None:
    assert timeout_module.seconds_until(None) == 0


# --- Inactivity nudge/auto-advance timers -----------------------------------


def test_inactivity_nudge_job_name_is_stable_and_unique_per_game() -> None:
    assert timeout_module.inactivity_nudge_job_name(42) == timeout_module.inactivity_nudge_job_name(
        42
    )
    assert timeout_module.inactivity_nudge_job_name(42) != timeout_module.inactivity_nudge_job_name(
        43
    )


def test_inactivity_advance_job_name_is_stable_and_unique_per_game() -> None:
    assert timeout_module.inactivity_advance_job_name(
        42
    ) == timeout_module.inactivity_advance_job_name(42)
    assert timeout_module.inactivity_advance_job_name(
        42
    ) != timeout_module.inactivity_advance_job_name(43)


def test_schedule_inactivity_timers_schedules_both_jobs_from_stored_deadlines(
    session_factory,
) -> None:
    game_id = _active_game(
        session_factory,
        inactivity_nudge_at=datetime.now(UTC) + timedelta(hours=3),
        inactivity_advance_at=datetime.now(UTC) + timedelta(hours=6),
    )
    job_queue = MagicMock()
    job_queue.get_jobs_by_name.return_value = []
    with session_factory() as session:
        game = session.get(Game, game_id)
        timeout_module.schedule_inactivity_timers(job_queue, game)

    names = [call.kwargs["name"] for call in job_queue.run_once.call_args_list]
    assert timeout_module.inactivity_nudge_job_name(game_id) in names
    assert timeout_module.inactivity_advance_job_name(game_id) in names


def test_schedule_inactivity_timers_cancels_existing_ones_first(session_factory) -> None:
    # Same guard schedule_turn_timers uses — resetting the clock on every
    # guess must never double-schedule.
    game_id = _active_game(
        session_factory,
        inactivity_nudge_at=datetime.now(UTC) + timedelta(hours=3),
        inactivity_advance_at=datetime.now(UTC) + timedelta(hours=6),
    )
    job_queue = MagicMock()
    existing_job = MagicMock()
    job_queue.get_jobs_by_name.return_value = [existing_job]
    with session_factory() as session:
        game = session.get(Game, game_id)
        timeout_module.schedule_inactivity_timers(job_queue, game)

    assert existing_job.schedule_removal.call_count == 2  # nudge + advance


def test_schedule_inactivity_timers_is_a_noop_when_job_queue_is_none(session_factory) -> None:
    game_id = _active_game(session_factory)
    with session_factory() as session:
        game = session.get(Game, game_id)
        timeout_module.schedule_inactivity_timers(None, game)  # should not raise


def test_cancel_inactivity_timers_removes_both_named_jobs() -> None:
    job = MagicMock()
    job_queue = MagicMock()
    job_queue.get_jobs_by_name.return_value = [job]

    timeout_module.cancel_inactivity_timers(job_queue, 42)

    assert job_queue.get_jobs_by_name.call_count == 2
    assert job.schedule_removal.call_count == 2


def test_cancel_inactivity_timers_is_a_noop_when_job_queue_is_none() -> None:
    timeout_module.cancel_inactivity_timers(None, 42)  # should not raise


async def test_inactivity_nudge_job_callback_posts_a_message_replying_to_the_pinned_image(
    session_factory,
) -> None:
    game_id = _active_game(session_factory)
    with session_factory() as session:
        settings.set_pinned_message_id(session, 777)
        session.commit()
    context = _make_job_context(session_factory, game_id=game_id)
    context.bot.send_message = AsyncMock()

    await timeout_module.inactivity_nudge_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_message.assert_awaited_once()
    assert context.bot.send_message.await_args is not None
    _, kwargs = context.bot.send_message.await_args
    assert kwargs["chat_id"] == 555
    assert kwargs["reply_to_message_id"] == 777


async def test_inactivity_nudge_job_callback_clears_the_nudge_deadline_after_sending(
    session_factory,
) -> None:
    game_id = _active_game(session_factory)
    with session_factory() as session:
        game = session.get(Game, game_id)
        game_service.reset_inactivity_clock(game)
        session.commit()
    with session_factory() as session:
        # Refetched (rather than kept from the write above) so it round-
        # trips through SQLite as naive, matching what the callback's own
        # later read/compare sees.
        advance_at_before = session.get(Game, game_id).inactivity_advance_at
    context = _make_job_context(session_factory, game_id=game_id)
    context.bot.send_message = AsyncMock()

    await timeout_module.inactivity_nudge_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        # Cleared so a later redeploy's rearm_pending_timeouts doesn't
        # re-derive and re-fire the same already-sent nudge from a stale
        # past deadline.
        assert fetched.inactivity_nudge_at is None
        # Untouched — the auto-advance timer keeps its own independent 6h
        # deadline.
        assert fetched.inactivity_advance_at == advance_at_before


async def test_inactivity_nudge_job_callback_is_a_noop_if_not_active(session_factory) -> None:
    game_id = _active_game(session_factory, status=GameStatus.WON, winner_id=1)
    context = _make_job_context(session_factory, game_id=game_id)
    context.bot.send_message = AsyncMock()

    await timeout_module.inactivity_nudge_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_message.assert_not_awaited()


def _make_advance_job_context(session_factory, *, game_id: int) -> MagicMock:
    context = _make_job_context(session_factory, game_id=game_id)
    context.bot.get_file = AsyncMock(
        return_value=MagicMock(download_as_bytearray=AsyncMock(return_value=bytearray(b"fake")))
    )
    context.bot.send_photo = AsyncMock(return_value=MagicMock(message_id=999))
    context.bot.pin_chat_message = AsyncMock()
    context.bot.unpin_chat_message = AsyncMock()
    context.job_queue = MagicMock()
    context.job_queue.get_jobs_by_name.return_value = []
    return context


async def test_inactivity_advance_job_callback_advances_the_stage_and_reschedules(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("nani_pix_bot.services.pixelate.pixelate", lambda *_: b"pixelated")
    game_id = _active_game(session_factory, current_stage=PixelStage.STAGE_1)
    context = _make_advance_job_context(session_factory, game_id=game_id)

    await timeout_module.inactivity_advance_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_photo.assert_awaited_once()
    context.bot.pin_chat_message.assert_awaited_once()
    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.status == GameStatus.ACTIVE
        assert fetched.current_stage == PixelStage.STAGE_2
        assert fetched.inactivity_advance_at is not None
    # Rescheduled for the new stage.
    names = [call.kwargs["name"] for call in context.job_queue.run_once.call_args_list]
    assert timeout_module.inactivity_advance_job_name(game_id) in names


async def test_inactivity_advance_job_callback_ends_unsolved_on_final_stage(
    session_factory,
) -> None:
    game_id = _active_game(session_factory, current_stage=PixelStage.STAGE_5)
    context = _make_advance_job_context(session_factory, game_id=game_id)

    await timeout_module.inactivity_advance_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_photo.assert_awaited_once()
    _, kwargs = context.bot.send_photo.await_args
    assert kwargs["photo"] == b"file123"  # the original screenshot, not a pixelated one
    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.status == GameStatus.UNSOLVED
        assert fetched.original_image is None


async def test_inactivity_advance_job_callback_rolls_overthrow_on_unsolved(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_id = _active_game(session_factory, current_stage=PixelStage.STAGE_5)
    context = _make_advance_job_context(session_factory, game_id=game_id)
    calls = []

    async def fake_maybe_overthrow(context, session_factory, **kwargs):
        calls.append(kwargs)

    # Patched on autostart.py, not inactivity.py: inactivity_advance_job_callback
    # imports maybe_overthrow lazily (function-local) to dodge a real
    # circular import (see the docstring on inactivity_advance_job_callback),
    # so inactivity.py never has a module-level `maybe_overthrow` name to
    # patch — the lazy import re-reads autostart.maybe_overthrow fresh on
    # every call, which is exactly what makes it patchable here. Same
    # pattern as test_timeout_job_callback_rolls_overthrow for game_timeout.py.
    monkeypatch.setattr("nani_pix_bot.jobs.timers.autostart.maybe_overthrow", fake_maybe_overthrow)

    await timeout_module.inactivity_advance_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    assert len(calls) == 1
    assert calls[0].get("winner_id") is None


async def test_inactivity_advance_job_callback_keeps_stage_advance_when_post_times_out(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("nani_pix_bot.services.pixelate.pixelate", lambda *_: b"pixelated")
    game_id = _active_game(session_factory, current_stage=PixelStage.STAGE_1)
    context = _make_advance_job_context(session_factory, game_id=game_id)
    context.bot.send_photo = AsyncMock(side_effect=TimedOut())

    await timeout_module.inactivity_advance_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.status == GameStatus.ACTIVE
        assert fetched.current_stage == PixelStage.STAGE_2


async def test_inactivity_advance_job_callback_keeps_unsolved_ending_when_reveal_times_out(
    session_factory,
) -> None:
    game_id = _active_game(session_factory, current_stage=PixelStage.STAGE_5)
    context = _make_advance_job_context(session_factory, game_id=game_id)
    context.bot.send_photo = AsyncMock(side_effect=TimedOut())

    await timeout_module.inactivity_advance_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.status == GameStatus.UNSOLVED
        assert fetched.original_image == b"file123"


async def test_inactivity_advance_job_callback_is_a_noop_if_not_active(session_factory) -> None:
    game_id = _active_game(session_factory, status=GameStatus.WON, winner_id=1)
    context = _make_advance_job_context(session_factory, game_id=game_id)

    await timeout_module.inactivity_advance_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_photo.assert_not_awaited()


async def test_inactivity_advance_job_callback_raises_runtime_error_if_no_image(
    session_factory,
) -> None:
    """Genuine internal-invariant guard (issue #117): an ACTIVE game
    always has its original_image, but unlike guess.py/correct.py this
    background job callback never checked it before — it used to be a
    bare `assert`, silently stripped under `python -O`. Now it's a real
    RuntimeError, so a broken invariant doesn't fail silently in a
    background job with no synchronous caller to notice."""
    game_id = _active_game(session_factory, original_image=None)
    context = _make_advance_job_context(session_factory, game_id=game_id)

    with pytest.raises(RuntimeError, match="original_image is missing"):
        await timeout_module.inactivity_advance_job_callback(
            cast(ContextTypes.DEFAULT_TYPE, context)
        )

    context.bot.send_photo.assert_not_awaited()


async def test_inactivity_advance_job_callback_acts_on_hard_mode_game_with_no_current_stage(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for the load-bearing bug this task fixes: a
    hard-mode game's current_stage is always None (Task 4: it uses
    hard_mode_image_a/_b + hard_mode_turn instead), and the old guard
    (`game.current_stage is None`) silently no-opped every hard-mode
    game forever — never auto-advancing it. This must actually act, not
    no-op."""
    monkeypatch.setattr("nani_pix_bot.services.pixelate.pixelate", lambda *_: b"pixelated")
    game_id = _hard_mode_active_game(session_factory)
    context = _make_advance_job_context(session_factory, game_id=game_id)

    await timeout_module.inactivity_advance_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_media_group.assert_awaited_once()
    context.bot.send_photo.assert_not_awaited()


async def test_inactivity_advance_job_callback_advances_hard_mode_turn_and_reschedules(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    pixelate_mock = MagicMock(return_value=b"pixelated")
    monkeypatch.setattr("nani_pix_bot.services.pixelate.pixelate", pixelate_mock)
    game_id = _hard_mode_active_game(session_factory)
    context = _make_advance_job_context(session_factory, game_id=game_id)

    await timeout_module.inactivity_advance_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_media_group.assert_awaited_once()
    _, kwargs = context.bot.send_media_group.call_args
    media = kwargs["media"]
    assert len(media) == 2
    assert media[0].media.input_file_content == b"pixelated"
    assert media[1].media.input_file_content == b"pixelated"
    # Pixelated at turn 2's width (HARD_MODE_TURN_WIDTHS[2]), not turn 1's.
    assert [call.args[1] for call in pixelate_mock.call_args_list] == [160, 160]

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.status == GameStatus.ACTIVE
        assert fetched.hard_mode_turn == 2
        assert fetched.wrong_guess_count == 0
        assert fetched.inactivity_advance_at is not None
    names = [call.kwargs["name"] for call in context.job_queue.run_once.call_args_list]
    assert timeout_module.inactivity_advance_job_name(game_id) in names


async def test_inactivity_advance_job_callback_ends_hard_mode_unsolved_from_turn_two(
    session_factory,
) -> None:
    game_id = _hard_mode_active_game(session_factory, hard_mode_turn=2)
    context = _make_advance_job_context(session_factory, game_id=game_id)

    await timeout_module.inactivity_advance_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_media_group.assert_awaited_once()
    _, kwargs = context.bot.send_media_group.call_args
    media = kwargs["media"]
    assert media[0].media.input_file_content == b"hm-image-a"
    assert media[1].media.input_file_content == b"hm-image-b"
    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.status == GameStatus.UNSOLVED
        assert fetched.hard_mode_image_a is None
        assert fetched.hard_mode_image_b is None


async def test_inactivity_advance_job_callback_rolls_overthrow_on_hard_mode_unsolved(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_id = _hard_mode_active_game(session_factory, hard_mode_turn=2)
    context = _make_advance_job_context(session_factory, game_id=game_id)
    calls = []

    async def fake_maybe_overthrow(context, session_factory, **kwargs):
        calls.append(kwargs)

    # Patched on autostart.py, not inactivity.py — see the identical
    # non-hard-mode test above for why.
    monkeypatch.setattr("nani_pix_bot.jobs.timers.autostart.maybe_overthrow", fake_maybe_overthrow)

    await timeout_module.inactivity_advance_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    assert len(calls) == 1
    assert calls[0].get("winner_id") is None


async def test_inactivity_advance_job_callback_keeps_hard_mode_turn_advance_when_post_times_out(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("nani_pix_bot.services.pixelate.pixelate", lambda *_: b"pixelated")
    game_id = _hard_mode_active_game(session_factory)
    context = _make_advance_job_context(session_factory, game_id=game_id)
    context.bot.send_media_group = AsyncMock(side_effect=TimedOut())

    await timeout_module.inactivity_advance_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.status == GameStatus.ACTIVE
        assert fetched.hard_mode_turn == 2


async def test_inactivity_advance_job_callback_is_a_noop_for_hard_mode_game_if_not_active(
    session_factory,
) -> None:
    game_id = _hard_mode_active_game(session_factory, status=GameStatus.WON, winner_id=1)
    context = _make_advance_job_context(session_factory, game_id=game_id)

    await timeout_module.inactivity_advance_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_photo.assert_not_awaited()
    context.bot.send_media_group.assert_not_awaited()


def _make_post_image_context(session_factory) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    context.bot.send_photo = AsyncMock(return_value=MagicMock(message_id=999))
    context.bot.pin_chat_message = AsyncMock()
    context.bot.unpin_chat_message = AsyncMock()
    return context


async def test_post_current_image_pins_the_new_message_and_unpins_the_old_one(
    session_factory,
) -> None:
    context = _make_post_image_context(session_factory)
    with session_factory() as session:
        settings.set_pinned_message_id(session, 111)
        session.commit()

    message = await timeout_module.post_current_image(
        cast(ContextTypes.DEFAULT_TYPE, context),
        session_factory,
        photo=b"bytes",
        caption="a caption",
    )

    assert message is not None
    assert message.message_id == 999
    context.bot.unpin_chat_message.assert_awaited_once_with(chat_id=555, message_id=111)
    context.bot.pin_chat_message.assert_awaited_once()
    with session_factory() as session:
        assert settings.get_pinned_message_id(session) == 999


async def test_post_current_image_does_not_unpin_when_nothing_was_pinned_yet(
    session_factory,
) -> None:
    context = _make_post_image_context(session_factory)
    await timeout_module.post_current_image(
        cast(ContextTypes.DEFAULT_TYPE, context),
        session_factory,
        photo=b"bytes",
        caption="a caption",
    )

    context.bot.unpin_chat_message.assert_not_awaited()


async def test_post_current_image_does_not_raise_when_pinning_fails(session_factory) -> None:
    context = _make_post_image_context(session_factory)
    context.bot.pin_chat_message = AsyncMock(side_effect=BadRequest("Not enough rights"))
    message = await timeout_module.post_current_image(
        cast(ContextTypes.DEFAULT_TYPE, context),
        session_factory,
        photo=b"bytes",
        caption="a caption",
    )  # should not raise

    assert message is not None
    assert message.message_id == 999


async def test_post_current_image_does_not_raise_when_unpinning_the_old_message_fails(
    session_factory,
) -> None:
    context = _make_post_image_context(session_factory)
    context.bot.unpin_chat_message = AsyncMock(side_effect=BadRequest("Message to unpin not found"))
    with session_factory() as session:
        settings.set_pinned_message_id(session, 111)
        session.commit()

    await timeout_module.post_current_image(
        cast(ContextTypes.DEFAULT_TYPE, context),
        session_factory,
        photo=b"bytes",
        caption="a caption",
    )  # should not raise
    # Pinning the new message still happens despite the unpin failure.
    context.bot.pin_chat_message.assert_awaited_once()


async def test_post_current_image_returns_none_and_does_not_raise_when_send_photo_times_out(
    session_factory,
) -> None:
    context = _make_post_image_context(session_factory)
    context.bot.send_photo = AsyncMock(side_effect=TimedOut())

    message = await timeout_module.post_current_image(
        cast(ContextTypes.DEFAULT_TYPE, context),
        session_factory,
        photo=b"bytes",
        caption="a caption",
    )  # should not raise

    assert message is None
    context.bot.pin_chat_message.assert_not_awaited()
    context.bot.unpin_chat_message.assert_not_awaited()


def _make_post_images_context(session_factory) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    context.bot.send_media_group = AsyncMock(
        return_value=[MagicMock(message_id=998), MagicMock(message_id=999)]
    )
    context.bot.pin_chat_message = AsyncMock()
    context.bot.unpin_chat_message = AsyncMock()
    return context


async def test_post_current_images_sends_a_media_group_with_caption_on_first_photo_only(
    session_factory,
) -> None:
    context = _make_post_images_context(session_factory)

    await timeout_module.post_current_images(
        cast(ContextTypes.DEFAULT_TYPE, context),
        session_factory,
        photos=(b"photo-a", b"photo-b"),
        caption="a caption",
    )

    context.bot.send_media_group.assert_awaited_once()
    _, kwargs = context.bot.send_media_group.call_args
    assert kwargs["chat_id"] == 555
    assert kwargs["message_thread_id"] == 7
    media = kwargs["media"]
    assert len(media) == 2
    assert media[0].media.input_file_content == b"photo-a"
    assert media[0].caption == "a caption"
    assert media[1].media.input_file_content == b"photo-b"
    assert media[1].caption is None


async def test_post_current_images_pins_the_first_message_and_unpins_the_old_one(
    session_factory,
) -> None:
    context = _make_post_images_context(session_factory)
    with session_factory() as session:
        settings.set_pinned_message_id(session, 111)
        session.commit()

    result = await timeout_module.post_current_images(
        cast(ContextTypes.DEFAULT_TYPE, context),
        session_factory,
        photos=(b"photo-a", b"photo-b"),
        caption="a caption",
    )

    assert result is not None
    assert tuple(m.message_id for m in result) == (998, 999)
    context.bot.unpin_chat_message.assert_awaited_once_with(chat_id=555, message_id=111)
    context.bot.pin_chat_message.assert_awaited_once_with(
        chat_id=555, message_id=998, disable_notification=True
    )
    with session_factory() as session:
        assert settings.get_pinned_message_id(session) == 998


async def test_post_current_images_returns_none_and_leaves_pin_state_untouched_on_send_failure(
    session_factory,
) -> None:
    context = _make_post_images_context(session_factory)
    context.bot.send_media_group = AsyncMock(side_effect=TimedOut())
    with session_factory() as session:
        settings.set_pinned_message_id(session, 111)
        session.commit()

    result = await timeout_module.post_current_images(
        cast(ContextTypes.DEFAULT_TYPE, context),
        session_factory,
        photos=(b"photo-a", b"photo-b"),
        caption="a caption",
    )  # should not raise

    assert result is None
    context.bot.pin_chat_message.assert_not_awaited()
    context.bot.unpin_chat_message.assert_not_awaited()
    with session_factory() as session:
        assert settings.get_pinned_message_id(session) == 111


async def test_post_current_images_returns_none_and_does_not_raise_on_empty_send_result(
    session_factory,
) -> None:
    context = _make_post_images_context(session_factory)
    context.bot.send_media_group = AsyncMock(return_value=[])

    result = await timeout_module.post_current_images(
        cast(ContextTypes.DEFAULT_TYPE, context),
        session_factory,
        photos=(b"photo-a", b"photo-b"),
        caption="a caption",
    )  # should not raise (no result[0] indexing crash)

    assert result is None
    context.bot.pin_chat_message.assert_not_awaited()
    context.bot.unpin_chat_message.assert_not_awaited()


@pytest.mark.parametrize("sent_shape", ["single", "tuple"])
def test_clear_image_if_sent_clears_hard_mode_images_and_leaves_original_untouched(
    session_factory, sent_shape
) -> None:
    game_id = _active_game(
        session_factory,
        hard_mode=True,
        hard_mode_turn=1,
        hard_mode_image_a=b"image-a",
        hard_mode_image_b=b"image-b",
        original_image=b"should-not-be-touched",
    )
    sent = MagicMock(message_id=1) if sent_shape == "single" else (MagicMock(message_id=1),)

    timeout_module.clear_image_if_sent(session_factory, game_id, sent)

    with session_factory() as session:
        game = session.get(Game, game_id)
        assert game is not None
        assert game.hard_mode_image_a is None
        assert game.hard_mode_image_b is None
        assert game.original_image == b"should-not-be-touched"


def test_clear_image_if_sent_normal_game_clears_original_image(session_factory) -> None:
    game_id = _active_game(session_factory, original_image=b"should-be-cleared")
    sent = MagicMock(message_id=1)

    timeout_module.clear_image_if_sent(session_factory, game_id, sent)

    with session_factory() as session:
        game = session.get(Game, game_id)
        assert game is not None
        assert game.original_image is None
        assert game.hard_mode_image_a is None
        assert game.hard_mode_image_b is None


@pytest.mark.parametrize("hard_mode", [True, False])
def test_clear_image_if_sent_is_a_noop_when_sent_is_none(session_factory, hard_mode) -> None:
    game_id = _active_game(
        session_factory,
        hard_mode=hard_mode,
        hard_mode_turn=1 if hard_mode else None,
        hard_mode_image_a=b"image-a" if hard_mode else None,
        hard_mode_image_b=b"image-b" if hard_mode else None,
        original_image=None if hard_mode else b"should-stay",
    )

    timeout_module.clear_image_if_sent(session_factory, game_id, None)

    with session_factory() as session:
        game = session.get(Game, game_id)
        assert game is not None
        if hard_mode:
            assert game.hard_mode_image_a == b"image-a"
            assert game.hard_mode_image_b == b"image-b"
        else:
            assert game.original_image == b"should-stay"


async def test_rearm_pending_timeouts_also_reschedules_inactivity_timers(session_factory) -> None:
    game_id = _active_game(
        session_factory,
        inactivity_nudge_at=datetime.now(UTC) + timedelta(hours=3),
        inactivity_advance_at=datetime.now(UTC) + timedelta(hours=6),
    )
    job_queue = MagicMock()
    job_queue.get_jobs_by_name.return_value = []

    await timeout_module.rearm_pending_timeouts(job_queue, session_factory)

    names = [call.kwargs["name"] for call in job_queue.run_once.call_args_list]
    assert timeout_module.inactivity_nudge_job_name(game_id) in names
    assert timeout_module.inactivity_advance_job_name(game_id) in names


async def test_rearm_pending_timeouts_reschedules_idle_autostart(session_factory) -> None:
    with session_factory() as session:
        session.add(
            TurnState(
                id=1,
                next_starter_id=None,
                turn_opened_at=datetime.now(UTC),
                autostart_deadline_at=datetime.now(UTC) + timedelta(hours=24),
            )
        )
        session.commit()
    job_queue = MagicMock()
    job_queue.get_jobs_by_name.return_value = []

    await timeout_module.rearm_pending_timeouts(job_queue, session_factory)

    names = [call.kwargs["name"] for call in job_queue.run_once.call_args_list]
    assert timeout_module.IDLE_AUTOSTART_JOB_NAME in names
