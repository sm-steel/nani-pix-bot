from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Update
from telegram.error import TimedOut
from telegram.ext import ContextTypes

from nani_pix_bot.commands.game_flow import correct as correct_command_module
from nani_pix_bot.models.enums import GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player


def _make_update(
    *,
    user_id: int = 1,
    chat_id: int = 555,
    thread_id: int | None = 7,
    args: list[str] | None = None,
) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
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
        "bot_username": "nani_pix_bot",
    }
    context.args = args or []
    context.job_queue.get_jobs_by_name.return_value = []
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
    """Fire-and-forget calls (correct.py uses context.application.create_task
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
            "title_english": "Frieren: Beyond Journey's End",
        }
        defaults.update(overrides)
        game = Game(**defaults)
        session.add(game)
        session.commit()
        return game.id


def _active_hard_mode_game(session_factory, **overrides) -> int:
    """The hard-mode analogue of _active_game — current_stage/original_image
    stay unset (a hard-mode game never sets them; it carries its fixed
    screenshot pair via hard_mode_image_a/_b instead) — same shape as
    test_guess.py's own _active_hard_mode_game fixture."""
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
            "title_english": "Frieren: Beyond Journey's End",
        }
        defaults.update(overrides)
        game = Game(**defaults)
        session.add(game)
        session.commit()
        return game.id


async def test_correct_command_ignores_outside_the_game_topic(session_factory) -> None:
    _active_game(session_factory)
    update = _make_update(thread_id=999, args=["@winner"])
    context = _make_context(session_factory, args=["@winner"])

    await correct_command_module.correct_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_not_awaited()
    context.bot.send_photo.assert_not_awaited()


async def test_correct_command_requires_a_username_argument(session_factory) -> None:
    _active_game(session_factory)
    update = _make_update(args=[])
    context = _make_context(session_factory, args=[])

    await correct_command_module.correct_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    assert "usage" in update.message.reply_text.await_args.args[0].lower()


async def test_correct_command_rejects_non_starters(session_factory) -> None:
    _active_game(session_factory)
    with session_factory() as session:
        session.add(Player(telegram_user_id=2, username="winner"))
        session.commit()

    update = _make_update(user_id=2, args=["@winner"])
    context = _make_context(session_factory, args=["@winner"])

    await correct_command_module.correct_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    assert "only" in update.message.reply_text.await_args.args[0].lower()
    context.bot.send_photo.assert_not_awaited()


async def test_correct_command_rejects_an_unknown_username(session_factory) -> None:
    _active_game(session_factory, total_guess_count=1)
    update = _make_update(user_id=1, args=["@stranger"])
    context = _make_context(session_factory, args=["@stranger"])

    await correct_command_module.correct_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args.args[0]
    assert "stranger" in text.lower()
    # The reply has to tell them where to go: a bare @handle is
    # auto-linked by Telegram, so no parse_mode is involved.
    assert "@nani_pix_bot" in text
    context.bot.send_photo.assert_not_awaited()


async def test_correct_command_rejects_targeting_the_bot_itself(session_factory) -> None:
    """The bot gets a real `players` row once it starts its first game
    (issue #159's autostart/overthrow) — addressable by username with no
    special-casing otherwise, which would award the bot itself a win and
    a leaderboard entry. See MECHANICS.md's "Bot-initiated games"."""
    _active_game(session_factory, total_guess_count=1)
    with session_factory() as session:
        session.add(Player(telegram_user_id=999, username="nani_pix_bot"))
        session.commit()

    update = _make_update(user_id=1, args=["@nani_pix_bot"])
    context = _make_context(session_factory, args=["@nani_pix_bot"])
    context.bot.id = 999

    await correct_command_module.correct_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    context.bot.send_photo.assert_not_awaited()
    # No win recorded for the bot — the game must remain ACTIVE.
    with session_factory() as session:
        fetched = session.query(Game).one()
        assert fetched.status == GameStatus.ACTIVE


async def test_correct_command_forces_a_win_for_the_named_player(session_factory) -> None:
    game_id = _active_game(session_factory, total_guess_count=1)
    with session_factory() as session:
        session.add(Player(telegram_user_id=2, username="winner"))
        session.commit()

    update = _make_update(user_id=1, args=["@winner"])
    context = _make_context(session_factory, args=["@winner"])

    await correct_command_module.correct_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.bot.send_photo.assert_awaited_once()
    _, kwargs = context.bot.send_photo.await_args
    assert kwargs["photo"] == b"file123"

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.status == GameStatus.WON
        assert fetched.winner_id == 2
        assert fetched.original_image is None

        winner = session.get(Player, 2)
        assert winner is not None
        assert winner.wins == 1


async def test_correct_command_keeps_the_win_committed_when_the_announcement_times_out(
    session_factory,
) -> None:
    game_id = _active_game(session_factory, total_guess_count=1)
    with session_factory() as session:
        session.add(Player(telegram_user_id=2, username="winner"))
        session.commit()

    update = _make_update(user_id=1, args=["@winner"])
    context = _make_context(session_factory, args=["@winner"])
    context.bot.send_photo = AsyncMock(side_effect=TimedOut())

    await correct_command_module.correct_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.status == GameStatus.WON
        assert fetched.winner_id == 2
        # The reveal never sent, so the "confirmed sent" cleanup gate
        # (MECHANICS.md's "Cleanup" note) must not have run either.
        assert fetched.original_image == b"file123"

        winner = session.get(Player, 2)
        assert winner is not None
        assert winner.wins == 1


async def test_correct_command_cancels_the_timeout_job(session_factory) -> None:
    game_id = _active_game(session_factory, total_guess_count=1)
    with session_factory() as session:
        session.add(Player(telegram_user_id=2, username="winner"))
        session.commit()

    update = _make_update(user_id=1, args=["@winner"])
    context = _make_context(session_factory, args=["@winner"])

    await correct_command_module.correct_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.job_queue.get_jobs_by_name.assert_any_call(
        correct_command_module.timeout_module.timeout_job_name(game_id)
    )


async def test_correct_command_cancels_the_inactivity_timers(session_factory) -> None:
    game_id = _active_game(session_factory, total_guess_count=1)
    with session_factory() as session:
        session.add(Player(telegram_user_id=2, username="winner"))
        session.commit()

    update = _make_update(user_id=1, args=["@winner"])
    context = _make_context(session_factory, args=["@winner"])

    await correct_command_module.correct_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.job_queue.get_jobs_by_name.assert_any_call(
        correct_command_module.timeout_module.inactivity_nudge_job_name(game_id)
    )
    context.job_queue.get_jobs_by_name.assert_any_call(
        correct_command_module.timeout_module.inactivity_advance_job_name(game_id)
    )


async def test_correct_command_caption_names_the_winner_and_schedules_turn_timers(
    session_factory,
) -> None:
    _active_game(session_factory, total_guess_count=1)
    with session_factory() as session:
        session.add(Player(telegram_user_id=2, username="winner"))
        session.commit()

    update = _make_update(user_id=1, args=["@winner"])
    context = _make_context(session_factory, args=["@winner"])

    await correct_command_module.correct_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    _, kwargs = context.bot.send_photo.await_args
    assert "winner" in kwargs["caption"]
    names = [call.kwargs["name"] for call in context.job_queue.run_once.call_args_list]
    assert correct_command_module.timeout_module.TURN_REMINDER_JOB_NAME in names
    assert correct_command_module.timeout_module.TURN_EXPIRY_JOB_NAME in names


async def test_correct_command_rejects_before_any_guess_was_made(session_factory) -> None:
    game_id = _active_game(session_factory, total_guess_count=0)
    with session_factory() as session:
        session.add(Player(telegram_user_id=2, username="winner"))
        session.commit()

    update = _make_update(user_id=1, args=["@winner"])
    context = _make_context(session_factory, args=["@winner"])

    await correct_command_module.correct_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    context.bot.send_photo.assert_not_awaited()
    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.status == GameStatus.ACTIVE


async def test_correct_command_allowed_after_at_least_one_guess(session_factory) -> None:
    game_id = _active_game(session_factory, total_guess_count=1)
    with session_factory() as session:
        session.add(Player(telegram_user_id=2, username="winner"))
        session.commit()

    update = _make_update(user_id=1, args=["@winner"])
    context = _make_context(session_factory, args=["@winner"])

    await correct_command_module.correct_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.status == GameStatus.WON


async def test_correct_calls_maybe_overthrow(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    async def fake_maybe_overthrow(context, session_factory, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("nani_pix_bot.jobs.timers.maybe_overthrow", fake_maybe_overthrow)
    _active_game(session_factory, total_guess_count=1)
    with session_factory() as session:
        session.add(Player(telegram_user_id=2, username="winner"))
        session.commit()

    update = _make_update(user_id=1, args=["@winner"])
    context = _make_context(session_factory, args=["@winner"])
    scheduled = _capture_scheduled_tasks(context)

    await correct_command_module.correct_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    # /correct must schedule maybe_overthrow via context.application.create_task
    # (fire-and-forget), not await it inline — see correct_command's comment.
    assert len(scheduled) == 1
    coro, kwargs = scheduled[0]
    assert kwargs.get("update") is update
    await coro

    assert len(calls) == 1
    assert calls[0]["winner_id"] == 2
    assert calls[0]["winner_name"] == "winner"


async def test_correct_command_drops_the_mention_when_the_bot_has_no_handle_yet(
    session_factory,
) -> None:
    """Same window as /skip's (#81): bot_data["bot_username"] is written
    by _post_init, so a reply built before that — or in any test — used
    to end on a bare "@". Nothing is lost by omitting the sentence: the
    player is being told to message the bot they are already talking
    about."""
    _active_game(session_factory, total_guess_count=1)
    update = _make_update(user_id=1, args=["@stranger"])
    context = _make_context(session_factory, args=["@stranger"])
    del context.bot_data["bot_username"]

    await correct_command_module.correct_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    text = update.message.reply_text.await_args.args[0]
    assert "stranger" in text.lower()
    assert "@" not in text.replace("@stranger", "")


async def test_correct_command_hard_mode_reveals_both_stored_screenshots(session_factory) -> None:
    """A hard-mode /correct win reveals the stored screenshot pair as a
    2-photo album (post_current_images), not the normal-mode single
    original_image reveal — the award amount itself (+2 for hard mode)
    is already covered by Task 3's force_win tests, so this only checks
    the announcement/reveal shape.

    This is also the regression test for the guard-bug pattern this plan
    keeps finding: before this task, _validate_active_game_for_starter's
    `if game.original_image is None: return None` guard silently
    rejected every hard-mode /correct (original_image is always None for
    a hard-mode game), so this test would have failed with a "no_game"-
    style reply and no reveal sent at all."""
    game_id = _active_hard_mode_game(session_factory, total_guess_count=1)
    with session_factory() as session:
        session.add(Player(telegram_user_id=2, username="winner"))
        session.commit()

    update = _make_update(user_id=1, args=["@winner"])
    context = _make_context(session_factory, args=["@winner"])

    await correct_command_module.correct_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.bot.send_media_group.assert_awaited_once()
    context.bot.send_photo.assert_not_awaited()
    _, kwargs = context.bot.send_media_group.await_args
    media = kwargs["media"]
    assert len(media) == 2
    assert media[0].media.input_file_content == b"image-a-bytes"
    assert media[1].media.input_file_content == b"image-b-bytes"
    expected_caption = correct_command_module.i18n.t(
        "correct.hard_mode_caption", "en", winner="winner", title="Frieren: Beyond Journey's End"
    )
    assert media[0].caption == expected_caption

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.status == GameStatus.WON
        assert fetched.winner_id == 2
        # Confirmed sent -> hard-mode image bytes cleared, same cleanup
        # gate as the normal-mode original_image path (clear_image_if_sent
        # dispatches on game.hard_mode).
        assert fetched.hard_mode_image_a is None
        assert fetched.hard_mode_image_b is None
