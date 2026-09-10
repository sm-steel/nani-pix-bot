from typing import cast
from unittest.mock import AsyncMock, MagicMock

from telegram import Update
from telegram.error import Forbidden
from telegram.ext import ContextTypes

from nani_pix_bot.commands import onboarding


def _make_context(session_factory, **extra_bot_data) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
        "bot_username": "nani_pix_bot",
        **extra_bot_data,
    }
    context.bot.send_message = AsyncMock()
    return context


def _make_dm_update(*, user_id: int = 1) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.type = "private"
    update.message.reply_text = AsyncMock()
    return update


def _make_group_update(*, user_id: int = 1, thread_id: int | None = 7) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.type = "supergroup"
    update.effective_chat.id = 555
    update.message.message_thread_id = thread_id
    update.effective_message = update.message
    update.message.reply_text = AsyncMock()
    return update


async def test_start_command_replies_in_dm(session_factory) -> None:
    update = _make_dm_update()
    context = _make_context(session_factory)

    await onboarding.start_command(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    update.message.reply_text.assert_awaited_once()


async def test_start_command_ignores_group_chats(session_factory) -> None:
    update = _make_group_update()
    context = _make_context(session_factory)

    await onboarding.start_command(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    update.message.reply_text.assert_not_awaited()


async def test_help_command_replies_directly_in_dm(session_factory) -> None:
    update = _make_dm_update()
    context = _make_context(session_factory)

    await onboarding.help_command(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    update.message.reply_text.assert_awaited_once()
    context.bot.send_message.assert_not_awaited()


async def test_help_command_ignores_group_messages_outside_the_game_topic(session_factory) -> None:
    update = _make_group_update(thread_id=999)
    context = _make_context(session_factory)

    await onboarding.help_command(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    update.message.reply_text.assert_not_awaited()
    context.bot.send_message.assert_not_awaited()


async def test_help_command_in_group_dms_the_asker(session_factory) -> None:
    update = _make_group_update()
    context = _make_context(session_factory)

    await onboarding.help_command(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_message.assert_awaited_once()
    _, kwargs = context.bot.send_message.await_args
    assert kwargs["chat_id"] == 1
    update.message.reply_text.assert_not_awaited()


async def test_help_command_in_group_falls_back_when_dm_fails(session_factory) -> None:
    update = _make_group_update()
    context = _make_context(session_factory)
    context.bot.send_message = AsyncMock(side_effect=Forbidden("bot was blocked"))

    await onboarding.help_command(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    update.message.reply_text.assert_awaited_once()
    reply_text = update.message.reply_text.await_args.args[0]
    assert "nani_pix_bot" in reply_text
