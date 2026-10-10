from io import BytesIO
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image
from telegram import Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from nani_pix_bot.commands import profile as profile_module
from nani_pix_bot.models.player import Player
from nani_pix_bot.seasons import registry
from nani_pix_bot.services.cards import CARD_SIZE


@pytest.fixture(autouse=True)
def fake_runs(monkeypatch):
    runs = registry.discover("tests.seasons.fake_runs")
    monkeypatch.setattr(registry, "all_runs", lambda: runs)
    registry.all_items.cache_clear()
    yield runs
    registry.all_items.cache_clear()


@pytest.fixture(autouse=True)
def avatar(monkeypatch) -> AsyncMock:
    fetch = AsyncMock(return_value=None)
    monkeypatch.setattr(profile_module, "fetch_avatar", fetch)
    return fetch


@pytest.fixture(autouse=True)
def players(session_factory) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1, username="me"))
        session.add(Player(telegram_user_id=7, username="Bob"))
        session.commit()


def _update(*, chat_type: str = ChatType.SUPERGROUP, thread_id: int | None = 7) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = 1
    update.effective_user.username = "me"
    update.effective_chat.id = 555 if chat_type != ChatType.PRIVATE else 1
    update.effective_chat.type = chat_type
    update.message.message_thread_id = thread_id
    update.effective_message = update.message
    update.message.reply_text = AsyncMock()
    update.message.reply_photo = AsyncMock()
    return update


def _context(session_factory, args: list[str]) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    context.args = args
    return context


async def _run(session_factory, update: MagicMock, args: list[str] | None = None) -> None:
    await profile_module.profile_command(
        cast(Update, update),
        cast(ContextTypes.DEFAULT_TYPE, _context(session_factory, args or [])),
    )


async def test_profile_in_the_topic_sends_a_card_photo_with_a_caption(session_factory) -> None:
    update = _update()
    await _run(session_factory, update)

    kwargs = update.message.reply_photo.await_args.kwargs
    assert Image.open(BytesIO(kwargs["photo"])).size == CARD_SIZE
    assert "@me" in kwargs["caption"]
    update.message.reply_text.assert_not_awaited()


async def test_profile_of_another_player_by_username(session_factory, avatar) -> None:
    update = _update(chat_type=ChatType.PRIVATE, thread_id=None)
    await _run(session_factory, update, ["@bob"])

    assert "@Bob" in update.message.reply_photo.await_args.kwargs["caption"]
    assert avatar.await_args.args[1] == 7


async def test_profile_of_an_unknown_player(session_factory) -> None:
    update = _update()
    await _run(session_factory, update, ["@nobody"])

    update.message.reply_photo.assert_not_awaited()
    assert "nobody" in update.message.reply_text.await_args.args[0]


async def test_profile_outside_the_topic_is_ignored(session_factory) -> None:
    update = _update(thread_id=99)
    await _run(session_factory, update)

    update.message.reply_photo.assert_not_awaited()
    update.message.reply_text.assert_not_awaited()
