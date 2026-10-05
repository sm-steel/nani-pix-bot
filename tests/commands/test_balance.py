from typing import cast
from unittest.mock import AsyncMock, MagicMock

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from nani_pix_bot.commands import balance as balance_module
from nani_pix_bot.models.player import Player


def _make_update(*, chat_type: str, thread_id: int | None = 7) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = 1
    update.effective_chat.id = 555 if chat_type != ChatType.PRIVATE else 1
    update.effective_chat.type = chat_type
    update.message.message_thread_id = thread_id
    update.effective_message = update.message
    update.message.reply_text = AsyncMock()
    return update


def _make_context(session_factory) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    return context


async def test_balance_in_dm_shows_amount(session_factory) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1, currency=123))
        session.commit()
    update = _make_update(chat_type=ChatType.PRIVATE, thread_id=None)

    await balance_module.balance_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _make_context(session_factory))
    )

    assert "123 💠" in update.message.reply_text.await_args.args[0]


async def test_balance_in_game_topic_works(session_factory) -> None:
    update = _make_update(chat_type=ChatType.SUPERGROUP)

    await balance_module.balance_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _make_context(session_factory))
    )

    assert "0 💠" in update.message.reply_text.await_args.args[0]


async def test_balance_ignored_in_other_topics(session_factory) -> None:
    update = _make_update(chat_type=ChatType.SUPERGROUP, thread_id=999)

    await balance_module.balance_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _make_context(session_factory))
    )

    update.message.reply_text.assert_not_awaited()


async def test_balance_is_logged_at_info(session_factory, records) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1, currency=123))
        session.commit()
    update = _make_update(chat_type=ChatType.PRIVATE, thread_id=None)
    update.effective_user.username = "bob"

    await balance_module.balance_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _make_context(session_factory))
    )

    assert ("INFO", "1 (@bob) checked /balance: 123 💠") in records
