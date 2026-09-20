from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Update
from telegram.error import TimedOut
from telegram.ext import ContextTypes

from nani_pix_bot.commands.game_flow import guess as guess_command_module
from nani_pix_bot.models.enums import GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.stage_config import StageConfig


def _make_update(
    *,
    user_id: int = 1,
    chat_id: int = 555,
    thread_id: int | None = 7,
    args: list[str] | None = None,
) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.username = "guesser"
    update.effective_user.full_name = "Guesser Name"
    update.effective_chat.id = chat_id
    update.message.message_thread_id = thread_id
    update.effective_message = update.message
    update.message.reply_text = AsyncMock()
    return update


def _make_context(session_factory, *, args: list[str] | None = None) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    context.args = args or []
    context.job_queue.get_jobs_by_name.return_value = []
    context.bot.get_file = AsyncMock()
    context.bot.get_file.return_value.download_as_bytearray = AsyncMock(
        return_value=bytearray(b"original-bytes")
    )
    context.bot.send_photo = AsyncMock(return_value=MagicMock(message_id=999))
    context.bot.send_media_group = AsyncMock(
        return_value=[MagicMock(message_id=998), MagicMock(message_id=999)]
    )
    context.bot.pin_chat_message = AsyncMock()
    context.bot.unpin_chat_message = AsyncMock()
    # Default: close the scheduled coroutine so tests that don't care about
    # maybe_overthrow's fire-and-forget scheduling don't leak "coroutine
    # was never awaited" warnings. Tests that DO care override this via
    # _capture_scheduled_tasks below.
    context.application.create_task = MagicMock(side_effect=lambda coro, **kw: coro.close())
    return context


def _capture_scheduled_tasks(context: MagicMock) -> list[tuple]:
    """Fire-and-forget calls (guess.py uses context.application.create_task
    rather than awaiting maybe_overthrow inline — see issue #159's final
    review) hand back an asyncio.Task that nothing here runs. Tests that
    need the scheduled coroutine's side effects capture and await it
    directly instead."""
    scheduled: list[tuple] = []
    context.application.create_task = MagicMock(
        side_effect=lambda coro, **kw: scheduled.append((coro, kw))
    )
    return scheduled


def _active_game(session_factory, **overrides) -> int:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.commit()
        defaults = {
            "starter_id": 1,
            "original_image": b"file123",
            "status": GameStatus.ACTIVE,
            "current_stage": PixelStage.STAGE_1,
            "wrong_guess_count": 0,
            "anilist_id": 99,
            "title_romaji": "Sousou no Frieren",
            "title_english": "Frieren: Beyond Journey's End",
            "synonyms": ["Frieren"],
        }
        defaults.update(overrides)
        game = Game(**defaults)
        session.add(game)
        session.commit()
        return game.id


def _active_hard_mode_game(session_factory, **overrides) -> int:
    """The hard-mode analogue of _active_game — current_stage stays None
    (hard-mode games never set it; they use hard_mode_turn instead) and
    hard_mode_image_a/_b carry the fixed screenshot pair, in place of
    original_image."""
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.commit()
        defaults = {
            "starter_id": 1,
            "status": GameStatus.ACTIVE,
            "hard_mode": True,
            "hard_mode_turn": 1,
            "hard_mode_image_a": b"image-a-bytes",
            "hard_mode_image_b": b"image-b-bytes",
            "wrong_guess_count": 0,
            "anilist_id": 99,
            "title_romaji": "Sousou no Frieren",
            "title_english": "Frieren: Beyond Journey's End",
            "synonyms": ["Frieren"],
        }
        defaults.update(overrides)
        game = Game(**defaults)
        session.add(game)
        session.commit()
        return game.id


def _seed_stage_limit(session_factory, stage: PixelStage, wrong_guess_limit: int) -> None:
    """Seeds an explicit stage_config row so a test's expected threshold
    doesn't depend on whatever services/stage_config.py's current
    DEFAULT_STAGE_CONFIG happens to be (which gets retuned often)."""
    with session_factory() as session:
        session.add(StageConfig(stage=stage, target_width=100, wrong_guess_limit=wrong_guess_limit))
        session.commit()


async def test_guess_command_ignores_outside_the_game_topic(session_factory) -> None:
    update = _make_update(thread_id=999, args=["frieren"])
    context = _make_context(session_factory, args=["frieren"])

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_not_awaited()
    context.bot.send_photo.assert_not_awaited()


async def test_guess_command_requires_a_guess_argument(session_factory) -> None:
    update = _make_update()
    context = _make_context(session_factory, args=[])

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    assert "usage" in update.message.reply_text.await_args.args[0].lower()


async def test_guess_command_replies_when_no_game_is_active(session_factory) -> None:
    update = _make_update()
    context = _make_context(session_factory, args=["frieren"])

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    assert "no game" in update.message.reply_text.await_args.args[0].lower()


async def test_guess_command_correct_guess_reveals_and_clears_file_id(session_factory) -> None:
    game_id = _active_game(session_factory)
    update = _make_update(user_id=2, args=["frieren"])
    context = _make_context(session_factory, args=["frieren"])

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.bot.send_photo.assert_awaited_once()
    _, kwargs = context.bot.send_photo.await_args
    assert kwargs["photo"] == b"file123"

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.status == GameStatus.WON
        assert fetched.original_image is None


async def test_guess_command_keeps_the_win_committed_when_the_announcement_times_out(
    session_factory,
) -> None:
    game_id = _active_game(session_factory)
    update = _make_update(user_id=2, args=["frieren"])
    context = _make_context(session_factory, args=["frieren"])
    context.bot.send_photo = AsyncMock(side_effect=TimedOut())

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.status == GameStatus.WON
        # The reveal never sent, so the "confirmed sent" cleanup gate
        # (MECHANICS.md's "Cleanup" note) must not have run either.
        assert fetched.original_image == b"file123"


async def test_guess_command_keeps_the_stage_advance_committed_when_the_post_times_out(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(guess_command_module.pixelate_service, "pixelate", lambda *_: b"x8-bytes")
    game_id = _active_game(
        session_factory, current_stage=PixelStage.STAGE_3, wrong_guess_count=2, total_guess_count=3
    )
    _seed_stage_limit(session_factory, PixelStage.STAGE_3, wrong_guess_limit=3)
    _seed_stage_limit(session_factory, PixelStage.STAGE_4, wrong_guess_limit=5)
    update = _make_update(user_id=2, args=["attack", "on", "titan"])
    context = _make_context(session_factory, args=["attack", "on", "titan"])
    context.bot.send_photo = AsyncMock(side_effect=TimedOut())

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.current_stage == PixelStage.STAGE_4
        assert fetched.wrong_guess_count == 0
        assert fetched.total_guess_count == 4


async def test_guess_command_keeps_the_unsolved_ending_committed_when_the_post_times_out(
    session_factory,
) -> None:
    game_id = _active_game(session_factory, current_stage=PixelStage.STAGE_5, wrong_guess_count=7)
    _seed_stage_limit(session_factory, PixelStage.STAGE_5, wrong_guess_limit=8)
    update = _make_update(user_id=2, args=["attack", "on", "titan"])
    context = _make_context(session_factory, args=["attack", "on", "titan"])
    context.bot.send_photo = AsyncMock(side_effect=TimedOut())

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.status == GameStatus.UNSOLVED
        assert fetched.original_image == b"file123"


async def test_guess_command_wrong_guess_advances_stage_with_new_image(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(guess_command_module.pixelate_service, "pixelate", lambda *_: b"x8-bytes")
    # STAGE_1/STAGE_2's limit is only 1, so a stage with headroom (STAGE_3,
    # given a limit of 3 here) is needed to exercise "some wrong guesses,
    # then advances".
    game_id = _active_game(
        session_factory, current_stage=PixelStage.STAGE_3, wrong_guess_count=2, total_guess_count=3
    )
    _seed_stage_limit(session_factory, PixelStage.STAGE_3, wrong_guess_limit=3)
    _seed_stage_limit(session_factory, PixelStage.STAGE_4, wrong_guess_limit=5)
    update = _make_update(user_id=2, args=["attack", "on", "titan"])
    context = _make_context(session_factory, args=["attack", "on", "titan"])

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.bot.send_photo.assert_awaited_once()
    _, kwargs = context.bot.send_photo.await_args
    assert kwargs["photo"] == b"x8-bytes"
    # New stage (4/5), this was guess #4 overall, 5 more wrong guesses
    # allowed before STAGE_5.
    assert "4/5" in kwargs["caption"]
    assert "4" in kwargs["caption"]
    assert "5" in kwargs["caption"]

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.current_stage == PixelStage.STAGE_4
        assert fetched.wrong_guess_count == 0
        assert fetched.total_guess_count == 4


async def test_guess_command_wrong_guess_below_threshold_does_not_post_a_new_image(
    session_factory,
) -> None:
    # STAGE_1's limit is only 1 (no "stays" case exists there anymore) —
    # STAGE_3, given a limit of 3 here, has headroom.
    _active_game(session_factory, current_stage=PixelStage.STAGE_3, wrong_guess_count=0)
    _seed_stage_limit(session_factory, PixelStage.STAGE_3, wrong_guess_limit=3)
    update = _make_update(user_id=2, args=["attack", "on", "titan"])
    context = _make_context(session_factory, args=["attack", "on", "titan"])

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.bot.send_photo.assert_not_awaited()
    update.message.reply_text.assert_awaited_once()


async def test_guess_command_stage_exhaustion_reveals_unsolved(session_factory) -> None:
    game_id = _active_game(session_factory, current_stage=PixelStage.STAGE_5, wrong_guess_count=7)
    _seed_stage_limit(session_factory, PixelStage.STAGE_5, wrong_guess_limit=8)
    update = _make_update(user_id=2, args=["attack", "on", "titan"])
    context = _make_context(session_factory, args=["attack", "on", "titan"])

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.bot.send_photo.assert_awaited_once()
    _, kwargs = context.bot.send_photo.await_args
    assert kwargs["photo"] == b"file123"

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.status == GameStatus.UNSOLVED
        assert fetched.original_image is None


async def test_guess_command_correct_guess_cancels_the_timeout_job(session_factory) -> None:
    game_id = _active_game(session_factory)
    update = _make_update(user_id=2, args=["frieren"])
    context = _make_context(session_factory, args=["frieren"])

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.job_queue.get_jobs_by_name.assert_any_call(
        guess_command_module.timeout_module.timeout_job_name(game_id)
    )


async def test_guess_command_rejects_the_starter_guessing_on_their_own_game(
    session_factory,
) -> None:
    game_id = _active_game(session_factory)
    update = _make_update(user_id=1, args=["frieren"])  # starter_id is 1 in _active_game
    context = _make_context(session_factory, args=["frieren"])

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    context.bot.send_photo.assert_not_awaited()
    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.status == GameStatus.ACTIVE
        assert fetched.total_guess_count == 0


async def test_guess_command_wrong_guess_below_threshold_replies_with_remaining_count(
    session_factory,
) -> None:
    # STAGE_4, given a limit of 5 here, has plenty of headroom to test a
    # mid-stage "N wrong guesses left" reply.
    _active_game(session_factory, current_stage=PixelStage.STAGE_4, wrong_guess_count=1)
    _seed_stage_limit(session_factory, PixelStage.STAGE_4, wrong_guess_limit=5)
    update = _make_update(user_id=2, args=["attack", "on", "titan"])
    context = _make_context(session_factory, args=["attack", "on", "titan"])

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.bot.send_photo.assert_not_awaited()
    update.message.reply_text.assert_awaited_once()
    reply_text = update.message.reply_text.await_args.args[0]
    assert "3" in reply_text  # 5 - 2 = 3 guesses left
    assert "4/5" in reply_text  # still on stage 4 of 5 (STAGE_4)


async def test_guess_command_wrong_guess_reply_uses_the_current_stages_own_threshold(
    session_factory,
) -> None:
    # STAGE_3, given a limit of 3 here (not STAGE_4's 5) — a
    # stage-agnostic "remaining" calculation would get this wrong.
    _active_game(session_factory, current_stage=PixelStage.STAGE_3, wrong_guess_count=0)
    _seed_stage_limit(session_factory, PixelStage.STAGE_3, wrong_guess_limit=3)
    update = _make_update(user_id=2, args=["attack", "on", "titan"])
    context = _make_context(session_factory, args=["attack", "on", "titan"])

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    reply_text = update.message.reply_text.await_args.args[0]
    assert "2" in reply_text  # 3 - 1 = 2 guesses left
    assert "3/5" in reply_text  # stage 3 of 5 (STAGE_3)


async def test_guess_command_won_caption_names_the_winner(session_factory) -> None:
    _active_game(session_factory)
    update = _make_update(user_id=2, args=["frieren"])
    context = _make_context(session_factory, args=["frieren"])

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    _, kwargs = context.bot.send_photo.await_args
    assert "Guesser Name" in kwargs["caption"]


async def test_guess_command_won_schedules_the_turn_reminder_and_expiry(session_factory) -> None:
    _active_game(session_factory)
    update = _make_update(user_id=2, args=["frieren"])
    context = _make_context(session_factory, args=["frieren"])

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    names = [call.kwargs["name"] for call in context.job_queue.run_once.call_args_list]
    assert guess_command_module.timeout_module.TURN_REMINDER_JOB_NAME in names
    assert guess_command_module.timeout_module.TURN_EXPIRY_JOB_NAME in names


async def test_guess_command_won_cancels_the_inactivity_timers(session_factory) -> None:
    game_id = _active_game(session_factory)
    update = _make_update(user_id=2, args=["frieren"])
    context = _make_context(session_factory, args=["frieren"])

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.job_queue.get_jobs_by_name.assert_any_call(
        guess_command_module.timeout_module.inactivity_nudge_job_name(game_id)
    )
    context.job_queue.get_jobs_by_name.assert_any_call(
        guess_command_module.timeout_module.inactivity_advance_job_name(game_id)
    )


async def test_guess_command_unsolved_cancels_the_inactivity_timers(session_factory) -> None:
    game_id = _active_game(session_factory, current_stage=PixelStage.STAGE_5, wrong_guess_count=7)
    _seed_stage_limit(session_factory, PixelStage.STAGE_5, wrong_guess_limit=8)
    update = _make_update(user_id=2, args=["attack", "on", "titan"])
    context = _make_context(session_factory, args=["attack", "on", "titan"])

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.job_queue.get_jobs_by_name.assert_any_call(
        guess_command_module.timeout_module.inactivity_advance_job_name(game_id)
    )


async def test_guess_command_wrong_guess_resets_and_reschedules_the_inactivity_clock(
    session_factory,
) -> None:
    game_id = _active_game(session_factory, current_stage=PixelStage.STAGE_4, wrong_guess_count=1)
    _seed_stage_limit(session_factory, PixelStage.STAGE_4, wrong_guess_limit=5)
    update = _make_update(user_id=2, args=["attack", "on", "titan"])
    context = _make_context(session_factory, args=["attack", "on", "titan"])

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    names = [call.kwargs["name"] for call in context.job_queue.run_once.call_args_list]
    assert guess_command_module.timeout_module.inactivity_nudge_job_name(game_id) in names
    assert guess_command_module.timeout_module.inactivity_advance_job_name(game_id) in names
    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.inactivity_nudge_at is not None
        assert fetched.inactivity_advance_at is not None


async def test_guess_command_stage_advanced_resets_and_reschedules_the_inactivity_clock(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(guess_command_module.pixelate_service, "pixelate", lambda *_: b"x8-bytes")
    game_id = _active_game(
        session_factory, current_stage=PixelStage.STAGE_3, wrong_guess_count=2, total_guess_count=3
    )
    _seed_stage_limit(session_factory, PixelStage.STAGE_3, wrong_guess_limit=3)
    _seed_stage_limit(session_factory, PixelStage.STAGE_4, wrong_guess_limit=5)
    update = _make_update(user_id=2, args=["attack", "on", "titan"])
    context = _make_context(session_factory, args=["attack", "on", "titan"])

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    names = [call.kwargs["name"] for call in context.job_queue.run_once.call_args_list]
    assert guess_command_module.timeout_module.inactivity_nudge_job_name(game_id) in names
    assert guess_command_module.timeout_module.inactivity_advance_job_name(game_id) in names


async def test_guess_command_wrong_feedback_shows_remaining_over_the_stage_limit(
    session_factory,
) -> None:
    _active_game(session_factory, current_stage=PixelStage.STAGE_3, wrong_guess_count=0)
    _seed_stage_limit(session_factory, PixelStage.STAGE_3, wrong_guess_limit=3)
    update = _make_update(user_id=2, args=["attack", "on", "titan"])
    context = _make_context(session_factory, args=["attack", "on", "titan"])

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    text = update.message.reply_text.await_args.args[0]
    assert "2/3" in text


async def test_guess_won_calls_maybe_overthrow(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    async def fake_maybe_overthrow(context, session_factory, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("nani_pix_bot.jobs.timers.maybe_overthrow", fake_maybe_overthrow)
    _active_game(session_factory)
    update = _make_update(user_id=2, args=["frieren"])
    context = _make_context(session_factory, args=["frieren"])
    scheduled = _capture_scheduled_tasks(context)

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    # A WON outcome must be scheduled via context.application.create_task
    # (fire-and-forget), not awaited inline — see guess_command's comment.
    assert len(scheduled) == 1
    coro, kwargs = scheduled[0]
    assert kwargs.get("update") is update
    await coro

    assert len(calls) == 1
    assert calls[0]["winner_id"] == 2


async def test_guess_unsolved_calls_maybe_overthrow_with_no_winner(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    async def fake_maybe_overthrow(context, session_factory, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("nani_pix_bot.jobs.timers.maybe_overthrow", fake_maybe_overthrow)
    _active_game(session_factory, current_stage=PixelStage.STAGE_5, wrong_guess_count=7)
    _seed_stage_limit(session_factory, PixelStage.STAGE_5, wrong_guess_limit=8)
    update = _make_update(user_id=2, args=["attack", "on", "titan"])
    context = _make_context(session_factory, args=["attack", "on", "titan"])
    scheduled = _capture_scheduled_tasks(context)

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    assert len(scheduled) == 1
    coro, kwargs = scheduled[0]
    assert kwargs.get("update") is update
    await coro

    assert len(calls) == 1
    assert calls[0].get("winner_id") is None


async def test_guess_command_stage_advance_caption_shows_the_new_stage_budget(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The advance caption names the stage's whole budget, not a bare
    cumulative guess counter with nothing to measure it against."""
    monkeypatch.setattr(guess_command_module.pixelate_service, "pixelate", lambda *_: b"x8-bytes")
    _active_game(session_factory, current_stage=PixelStage.STAGE_2, wrong_guess_count=0)
    _seed_stage_limit(session_factory, PixelStage.STAGE_2, wrong_guess_limit=1)
    _seed_stage_limit(session_factory, PixelStage.STAGE_3, wrong_guess_limit=2)
    update = _make_update(user_id=2, args=["attack", "on", "titan"])
    context = _make_context(session_factory, args=["attack", "on", "titan"])

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    _, kwargs = context.bot.send_photo.await_args
    assert "3/5" in kwargs["caption"]
    assert "2/2" in kwargs["caption"]
    assert "#" not in kwargs["caption"]


async def test_guess_command_hard_mode_won_posts_a_two_photo_album(session_factory) -> None:
    game_id = _active_hard_mode_game(session_factory)
    update = _make_update(user_id=2, args=["frieren"])
    context = _make_context(session_factory, args=["frieren"])

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.bot.send_media_group.assert_awaited_once()
    context.bot.send_photo.assert_not_awaited()
    _, kwargs = context.bot.send_media_group.await_args
    media = kwargs["media"]
    assert len(media) == 2
    assert media[0].media.input_file_content == b"image-a-bytes"
    assert media[1].media.input_file_content == b"image-b-bytes"
    # guess.hard_mode_won_caption has a variation pool (see
    # locales/variations/en.json) — a substring check on the dynamic
    # values, not exact equality against a second independent i18n.t()
    # call, since that second call can legitimately pick a different
    # (equally valid) phrasing from the pool than the one guess_command
    # itself rendered. Same pool-aware style as
    # test_guess_command_won_caption_names_the_winner above.
    assert "Guesser Name" in media[0].caption
    assert "Frieren: Beyond Journey's End" in media[0].caption

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.status == GameStatus.WON
        winner = session.get(Player, 2)
        assert winner is not None
        assert winner.wins == 2
        # Confirmed sent -> hard-mode image bytes cleared, same cleanup
        # gate as the normal-mode original_image path.
        assert fetched.hard_mode_image_a is None
        assert fetched.hard_mode_image_b is None


async def test_guess_command_hard_mode_turn_advanced_posts_album_at_turn_two_width(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    pixelate_calls: list[tuple] = []

    def fake_pixelate(image_bytes, width, algorithm):
        pixelate_calls.append((image_bytes, width, algorithm))
        return b"pixelated-" + image_bytes

    monkeypatch.setattr(guess_command_module.pixelate_service, "pixelate", fake_pixelate)
    game_id = _active_hard_mode_game(session_factory)
    update = _make_update(user_id=2, args=["attack", "on", "titan"])
    context = _make_context(session_factory, args=["attack", "on", "titan"])

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.bot.send_media_group.assert_awaited_once()
    context.bot.send_photo.assert_not_awaited()
    turn_two_width = guess_command_module.game_service.HARD_MODE_TURN_WIDTHS[2]
    assert all(width == turn_two_width for _, width, _ in pixelate_calls)
    _, kwargs = context.bot.send_media_group.await_args
    media = kwargs["media"]
    assert media[0].media.input_file_content == b"pixelated-image-a-bytes"
    assert media[1].media.input_file_content == b"pixelated-image-b-bytes"
    # guess.hard_mode_turn_advanced_caption also has a variation pool —
    # same pool-aware substring reasoning as the won-caption test above,
    # checking the interpolated stage/total and remaining/limit numbers
    # rather than exact-matching a second, independently-random t() call.
    assert "2/2" in media[0].caption
    assert "1/1" in media[0].caption

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.status == GameStatus.ACTIVE
        assert fetched.hard_mode_turn == 2
        assert fetched.wrong_guess_count == 0


async def test_guess_command_hard_mode_unsolved_reveals_two_photo_album(
    session_factory,
) -> None:
    game_id = _active_hard_mode_game(session_factory, hard_mode_turn=2)
    update = _make_update(user_id=2, args=["attack", "on", "titan"])
    context = _make_context(session_factory, args=["attack", "on", "titan"])

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.bot.send_media_group.assert_awaited_once()
    context.bot.send_photo.assert_not_awaited()
    _, kwargs = context.bot.send_media_group.await_args
    media = kwargs["media"]
    assert media[0].media.input_file_content == b"image-a-bytes"
    assert media[1].media.input_file_content == b"image-b-bytes"
    expected_caption = guess_command_module.i18n.t(
        "guess.hard_mode_unsolved_caption", "en", title="Frieren: Beyond Journey's End"
    )
    assert media[0].caption == expected_caption

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.status == GameStatus.UNSOLVED
        assert fetched.hard_mode_image_a is None
        assert fetched.hard_mode_image_b is None


async def test_guess_command_hard_mode_wrong_feedback_uses_turn_progress(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercises the WRONG branch's hard-mode caption directly — per
    Task 3's note, HARD_MODE_WRONG_GUESS_LIMIT == 1 means a real /guess
    can never actually produce this outcome (see the regression test
    below), but the branch must still exist and be correct."""

    def fake_record_guess(session, game, *, guesser_id, guess_text):
        return guess_command_module.game_service.GuessOutcome.WRONG

    monkeypatch.setattr(guess_command_module.game_service, "record_guess", fake_record_guess)
    _active_hard_mode_game(session_factory)
    update = _make_update(user_id=2, args=["attack", "on", "titan"])
    context = _make_context(session_factory, args=["attack", "on", "titan"])

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.bot.send_media_group.assert_not_awaited()
    context.bot.send_photo.assert_not_awaited()
    update.message.reply_text.assert_awaited_once()
    expected_text = guess_command_module.i18n.t(
        "guess.hard_mode_wrong_feedback", "en", remaining=1, limit=1, stage=1, total=2
    )
    assert update.message.reply_text.await_args.args[0] == expected_text


async def test_guess_command_hard_mode_wrong_guess_never_actually_fires(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for Task 3's documented consequence of
    HARD_MODE_WRONG_GUESS_LIMIT == 1: a real wrong /guess against a
    hard-mode game always immediately advances the turn (or ends the
    game unsolved on the last turn) — WRONG is never the outcome a real
    player sees, even though the branch exists (see the test above)."""
    monkeypatch.setattr(guess_command_module.pixelate_service, "pixelate", lambda *_: b"x8-bytes")
    _active_hard_mode_game(session_factory)
    update = _make_update(user_id=2, args=["attack", "on", "titan"])
    context = _make_context(session_factory, args=["attack", "on", "titan"])

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    # A WRONG outcome would have replied with plain text and posted no
    # image; TURN_ADVANCED posts the 2-photo album instead.
    context.bot.send_media_group.assert_awaited_once()
    update.message.reply_text.assert_not_awaited()
