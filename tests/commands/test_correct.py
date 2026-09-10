from typing import cast
from unittest.mock import AsyncMock, MagicMock

from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands import correct as correct_command_module
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
    }
    context.args = args or []
    context.job_queue.get_jobs_by_name.return_value = []
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
    assert "stranger" in update.message.reply_text.await_args.args[0].lower()
    context.bot.send_photo.assert_not_awaited()


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
    assert kwargs["photo"] == "file123"

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.status == GameStatus.WON
        assert fetched.winner_id == 2
        assert fetched.original_file_id is None

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
        correct_command_module.game_service.timeout_job_name(game_id)
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
