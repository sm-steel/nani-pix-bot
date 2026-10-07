from typing import cast
from unittest.mock import AsyncMock, MagicMock

from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands import leaderboard as leaderboard_command_module
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.player import Player


def _make_update(*, chat_id: int = 555, thread_id: int | None = 7) -> MagicMock:
    update = MagicMock()
    update.effective_chat.id = chat_id
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


async def test_leaderboard_command_ignores_outside_the_game_topic(session_factory) -> None:
    update = _make_update(thread_id=999)
    context = _make_context(session_factory)

    await leaderboard_command_module.leaderboard_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_not_awaited()


async def test_leaderboard_command_lists_winners_in_order(session_factory) -> None:
    with session_factory() as session:
        session.add_all(
            [
                Player(telegram_user_id=1, username="low", wins=1),
                Player(telegram_user_id=2, username="high", wins=5),
            ]
        )
        session.commit()

    update = _make_update()
    context = _make_context(session_factory)

    await leaderboard_command_module.leaderboard_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    reply_text = update.message.reply_text.await_args.args[0]
    assert reply_text.index("high") < reply_text.index("low")


async def test_leaderboard_command_handles_no_winners_yet(session_factory) -> None:
    update = _make_update()
    context = _make_context(session_factory)

    await leaderboard_command_module.leaderboard_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    assert "no" in update.message.reply_text.await_args.args[0].lower()


async def test_leaderboard_command_shows_currency(session_factory) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1, username="rich", wins=2, currency=77))
        session.commit()
    update = _make_update()

    await leaderboard_command_module.leaderboard_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _make_context(session_factory))
    )

    assert "77 💠" in update.message.reply_text.await_args.args[0]


async def test_a_chosen_title_shows_next_to_the_name(session_factory) -> None:
    with session_scope(session_factory) as session:
        session.add(Player(telegram_user_id=1, username="alice", wins=3, title_key="pioneer:1:"))
    update = _make_update()

    await leaderboard_command_module.leaderboard_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _make_context(session_factory))
    )

    assert "alice «Pioneer»" in update.message.reply_text.await_args.args[0]
