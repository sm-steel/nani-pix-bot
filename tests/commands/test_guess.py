from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands import guess as guess_command_module
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
    context.bot.send_photo = AsyncMock()
    return context


def _active_game(session_factory, **overrides) -> int:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.commit()
        defaults = {
            "starter_id": 1,
            "original_file_id": "file123",
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
    assert kwargs["photo"] == "file123"

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.status == GameStatus.WON
        assert fetched.original_file_id is None


async def test_guess_command_wrong_guess_advances_stage_with_new_image(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        guess_command_module.pixelate_service, "pixelate", lambda data, width: b"x8-bytes"
    )
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

    context.bot.get_file.assert_awaited_once_with("file123")
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
    assert kwargs["photo"] == "file123"

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.status == GameStatus.UNSOLVED
        assert fetched.original_file_id is None


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
