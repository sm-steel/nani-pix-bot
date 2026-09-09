from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands import guess as guess_command_module
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
    update.effective_user.username = "guesser"
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
            "current_stage": PixelStage.X10,
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
    update = _make_update(args=["frieren"])
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
        guess_command_module.pixelate_service, "pixelate", lambda data, stage: b"x8-bytes"
    )
    game_id = _active_game(session_factory, wrong_guess_count=4)
    update = _make_update(args=["attack", "on", "titan"])
    context = _make_context(session_factory, args=["attack", "on", "titan"])

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.bot.get_file.assert_awaited_once_with("file123")
    context.bot.send_photo.assert_awaited_once()
    _, kwargs = context.bot.send_photo.await_args
    assert kwargs["photo"] == b"x8-bytes"

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.current_stage == PixelStage.X8
        assert fetched.wrong_guess_count == 0


async def test_guess_command_wrong_guess_below_threshold_sends_nothing(session_factory) -> None:
    _active_game(session_factory, wrong_guess_count=0)
    update = _make_update(args=["attack", "on", "titan"])
    context = _make_context(session_factory, args=["attack", "on", "titan"])

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.bot.send_photo.assert_not_awaited()
    update.message.reply_text.assert_not_awaited()


async def test_guess_command_stage_exhaustion_reveals_unsolved(session_factory) -> None:
    game_id = _active_game(session_factory, current_stage=PixelStage.X2, wrong_guess_count=4)
    update = _make_update(args=["attack", "on", "titan"])
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
    update = _make_update(args=["frieren"])
    context = _make_context(session_factory, args=["frieren"])

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.job_queue.get_jobs_by_name.assert_called_once_with(
        guess_command_module.game_service.timeout_job_name(game_id)
    )
