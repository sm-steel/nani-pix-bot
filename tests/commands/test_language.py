from typing import cast
from unittest.mock import AsyncMock, MagicMock

from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes

from nani_pix_bot.commands import language as language_module
from nani_pix_bot.models.bot_settings import BotSettings


def _make_context(session_factory, *, admin: bool = True) -> MagicMock:
    context = MagicMock()
    context.bot_data = {"session_factory": session_factory, "group_chat_id": 555}
    status = ChatMemberStatus.ADMINISTRATOR if admin else ChatMemberStatus.MEMBER
    context.bot.get_chat_member = AsyncMock(return_value=MagicMock(status=status))
    context.bot.set_my_commands = AsyncMock()
    return context


def _make_update(*, user_id: int = 1) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.type = "private"
    update.message.reply_text = AsyncMock()
    return update


async def test_language_command_rejects_non_admins(session_factory) -> None:
    update = _make_update()
    context = _make_context(session_factory, admin=False)

    await language_module.language_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    assert "reply_markup" not in update.message.reply_text.await_args.kwargs


async def test_language_command_shows_keyboard_to_admins(session_factory) -> None:
    update = _make_update()
    context = _make_context(session_factory, admin=True)

    await language_module.language_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    _, kwargs = update.message.reply_text.await_args
    buttons = kwargs["reply_markup"].inline_keyboard
    assert {b.callback_data for row in buttons for b in row} == {
        "set_language:RU",
        "set_language:EN",
    }


def _make_callback_update(*, data: str, user_id: int = 1) -> MagicMock:
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.from_user.id = user_id
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


async def test_language_callback_sets_the_language_for_admins(session_factory) -> None:
    update = _make_callback_update(data="set_language:RU")
    context = _make_context(session_factory, admin=True)

    await language_module.language_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        fetched = session.get(BotSettings, 1)
        assert fetched is not None
        assert fetched.language == "RU"
    update.callback_query.edit_message_text.assert_awaited_once()


async def test_language_callback_ignores_non_admins(session_factory) -> None:
    update = _make_callback_update(data="set_language:RU")
    context = _make_context(session_factory, admin=False)

    await language_module.language_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        assert session.get(BotSettings, 1) is None
    update.callback_query.edit_message_text.assert_not_awaited()


async def test_language_callback_refreshes_the_command_menu(session_factory) -> None:
    update = _make_callback_update(data="set_language:RU")
    context = _make_context(session_factory, admin=True)
    context.bot.set_my_commands = AsyncMock()

    await language_module.language_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    assert context.bot.set_my_commands.await_count == 2
