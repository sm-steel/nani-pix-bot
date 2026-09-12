from typing import cast
from unittest.mock import AsyncMock, MagicMock

from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes

from nani_pix_bot.commands.game_flow import stop as stop_command_module
from nani_pix_bot.commands.helpers.keyboards import (
    STOP_CANCEL_CALLBACK_DATA,
    STOP_CONFIRM_CALLBACK_DATA,
)
from nani_pix_bot.models.enums import GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.turn_state import TurnState


def _make_update(
    *, user_id: int = 1, full_name: str = "Someone", chat_type: str = "private"
) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.full_name = full_name
    update.effective_chat.type = chat_type
    update.message.reply_text = AsyncMock()
    return update


def _make_context(session_factory, *, admin_ids: set[int] | None = None) -> MagicMock:
    admin_ids = admin_ids or set()
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    context.args = []
    context.job_queue.get_jobs_by_name.return_value = []
    context.bot.send_message = AsyncMock()

    async def _get_chat_member(_chat_id, user_id):
        status = ChatMemberStatus.ADMINISTRATOR if user_id in admin_ids else ChatMemberStatus.MEMBER
        return MagicMock(status=status)

    context.bot.get_chat_member = AsyncMock(side_effect=_get_chat_member)
    return context


def _active_game(session_factory, *, starter_id: int = 1, **overrides) -> int:
    with session_factory() as session:
        session.add(Player(telegram_user_id=starter_id))
        session.commit()
        defaults = {
            "starter_id": starter_id,
            "original_file_id": "file123",
            "status": GameStatus.ACTIVE,
            "current_stage": PixelStage.STAGE_1,
            "title_english": "Frieren: Beyond Journey's End",
        }
        defaults.update(overrides)
        game = Game(**defaults)
        session.add(game)
        session.commit()
        return game.id


def _setup_game(session_factory, *, starter_id: int = 1) -> int:
    with session_factory() as session:
        session.add(Player(telegram_user_id=starter_id))
        session.commit()
        game = Game(starter_id=starter_id, original_file_id="file123", status=GameStatus.SETUP)
        session.add(game)
        session.commit()
        return game.id


async def test_stop_command_ignores_group_chat_messages(session_factory) -> None:
    _active_game(session_factory)
    update = _make_update(chat_type="supergroup")
    context = _make_context(session_factory)

    await stop_command_module.stop_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_not_awaited()


async def test_stop_command_replies_when_no_game_is_running(session_factory) -> None:
    update = _make_update(user_id=1)
    context = _make_context(session_factory)

    await stop_command_module.stop_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    _, kwargs = update.message.reply_text.await_args
    assert "reply_markup" not in kwargs


async def test_stop_command_shows_confirmation_for_the_starter(session_factory) -> None:
    _active_game(session_factory, starter_id=1)
    update = _make_update(user_id=1)
    context = _make_context(session_factory)

    await stop_command_module.stop_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    _, kwargs = update.message.reply_text.await_args
    callbacks = [
        button.callback_data for row in kwargs["reply_markup"].inline_keyboard for button in row
    ]
    assert STOP_CONFIRM_CALLBACK_DATA in callbacks
    assert STOP_CANCEL_CALLBACK_DATA in callbacks


async def test_stop_command_shows_confirmation_for_a_group_admin(session_factory) -> None:
    _active_game(session_factory, starter_id=1)
    update = _make_update(user_id=2)
    context = _make_context(session_factory, admin_ids={2})

    await stop_command_module.stop_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    _, kwargs = update.message.reply_text.await_args
    assert "reply_markup" in kwargs


async def test_stop_command_rejects_a_non_starter_non_admin(session_factory) -> None:
    _active_game(session_factory, starter_id=1)
    update = _make_update(user_id=2)
    context = _make_context(session_factory)

    await stop_command_module.stop_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    _, kwargs = update.message.reply_text.await_args
    assert "reply_markup" not in kwargs
    with session_factory() as session:
        assert session.query(Game).count() == 1


def _make_callback_update(*, data: str, user_id: int = 1) -> MagicMock:
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.from_user.id = user_id
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


async def test_stop_callback_handler_cancel_leaves_the_game_untouched(session_factory) -> None:
    game_id = _active_game(session_factory, starter_id=1)
    update = _make_callback_update(data=STOP_CANCEL_CALLBACK_DATA, user_id=1)
    context = _make_context(session_factory)

    await stop_command_module.stop_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.callback_query.edit_message_text.assert_awaited_once()
    with session_factory() as session:
        assert session.get(Game, game_id) is not None


async def test_stop_callback_handler_confirm_deletes_active_game_and_notifies_group(
    session_factory,
) -> None:
    game_id = _active_game(session_factory, starter_id=1)
    update = _make_callback_update(data=STOP_CONFIRM_CALLBACK_DATA, user_id=1)
    context = _make_context(session_factory)

    await stop_command_module.stop_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        assert session.get(Game, game_id) is None
        turn_state = session.get(TurnState, 1)
        assert turn_state is None or turn_state.next_starter_id is None

    context.bot.send_message.assert_awaited_once()
    _, kwargs = context.bot.send_message.await_args
    assert kwargs["chat_id"] == 555
    update.callback_query.edit_message_text.assert_awaited_once()


async def test_stop_callback_handler_confirm_deletes_setup_game(session_factory) -> None:
    game_id = _setup_game(session_factory, starter_id=1)
    update = _make_callback_update(data=STOP_CONFIRM_CALLBACK_DATA, user_id=1)
    context = _make_context(session_factory)

    await stop_command_module.stop_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        assert session.get(Game, game_id) is None


async def test_stop_callback_handler_confirm_rejects_a_non_starter_non_admin_tap(
    session_factory,
) -> None:
    game_id = _active_game(session_factory, starter_id=1)
    update = _make_callback_update(data=STOP_CONFIRM_CALLBACK_DATA, user_id=2)
    context = _make_context(session_factory)

    await stop_command_module.stop_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        assert session.get(Game, game_id) is not None
    context.bot.send_message.assert_not_awaited()
