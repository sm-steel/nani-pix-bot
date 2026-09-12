from typing import cast
from unittest.mock import AsyncMock, MagicMock

from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes

from nani_pix_bot.commands import gamesenabled as gamesenabled_module
from nani_pix_bot.models.bot_settings import BotSettings


def _make_update(*, user_id: int = 1, chat_type: str = "private") -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.type = chat_type
    update.message.reply_text = AsyncMock()
    return update


def _make_context(session_factory, *, admin_ids: set[int] | None = None, args=None) -> MagicMock:
    admin_ids = admin_ids or set()
    context = MagicMock()
    context.bot_data = {"session_factory": session_factory, "group_chat_id": 555}
    context.args = args or []

    async def _get_chat_member(_chat_id, user_id):
        status = ChatMemberStatus.ADMINISTRATOR if user_id in admin_ids else ChatMemberStatus.MEMBER
        return MagicMock(status=status)

    context.bot.get_chat_member = AsyncMock(side_effect=_get_chat_member)
    return context


async def test_setgamesenabled_rejects_non_admin(session_factory) -> None:
    update = _make_update(user_id=2)
    context = _make_context(session_factory, args=["off"])

    await gamesenabled_module.setgamesenabled_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    assert "admin" in update.message.reply_text.await_args.args[0].lower()
    with session_factory() as session:
        assert session.get(BotSettings, 1) is None


async def test_setgamesenabled_ignores_group_chat(session_factory) -> None:
    update = _make_update(user_id=1, chat_type="supergroup")
    context = _make_context(session_factory, admin_ids={1}, args=["off"])

    await gamesenabled_module.setgamesenabled_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_not_awaited()


async def test_setgamesenabled_rejects_invalid_argument(session_factory) -> None:
    update = _make_update(user_id=1)
    context = _make_context(session_factory, admin_ids={1}, args=["maybe"])

    await gamesenabled_module.setgamesenabled_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    assert "usage" in update.message.reply_text.await_args.args[0].lower()


async def test_setgamesenabled_off_disables_games(session_factory) -> None:
    update = _make_update(user_id=1)
    context = _make_context(session_factory, admin_ids={1}, args=["off"])

    await gamesenabled_module.setgamesenabled_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        fetched = session.get(BotSettings, 1)
        assert fetched is not None
        assert fetched.games_enabled is False
    update.message.reply_text.assert_awaited_once()


async def test_setgamesenabled_on_enables_games(session_factory) -> None:
    with session_factory() as session:
        session.add(BotSettings(id=1, games_enabled=False))
        session.commit()

    update = _make_update(user_id=1)
    context = _make_context(session_factory, admin_ids={1}, args=["on"])

    await gamesenabled_module.setgamesenabled_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        fetched = session.get(BotSettings, 1)
        assert fetched is not None
        assert fetched.games_enabled is True
