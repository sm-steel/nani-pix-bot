from io import BytesIO
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image
from telegram import Update
from telegram.constants import ChatMemberStatus, ChatType
from telegram.ext import ContextTypes

from nani_pix_bot.commands import profile as profile_module
from nani_pix_bot.commands.profile import card as card_module
from nani_pix_bot.commands.profile import data as cb
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import GameStatus, ShowcaseKind
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.seasons import registry
from nani_pix_bot.services import i18n
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
    monkeypatch.setattr(card_module, "fetch_avatar", fetch)
    return fetch


@pytest.fixture(autouse=True)
def players(session_factory) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1, username="me"))
        session.add(Player(telegram_user_id=7, username="Bob"))
        session.flush()
        # Bob has played a game, so he's in the roster; "me" is the viewer.
        session.add(Game(starter_id=7, status=GameStatus.UNSOLVED, title_english="X"))
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


def _context(session_factory, args: list[str], *, member: bool = True) -> MagicMock:
    context = MagicMock()
    joined = ChatMemberStatus.MEMBER if member else ChatMemberStatus.LEFT
    context.bot.get_chat_member = AsyncMock(return_value=MagicMock(status=joined))
    context.bot.id = 999
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    context.args = args
    return context


async def _run(
    session_factory, update: MagicMock, args: list[str] | None = None, *, member: bool = True
) -> None:
    await profile_module.profile_command(
        cast(Update, update),
        cast(ContextTypes.DEFAULT_TYPE, _context(session_factory, args or [], member=member)),
    )


async def test_profile_in_the_topic_sends_a_card_photo_with_a_caption(session_factory) -> None:
    update = _update()
    await _run(session_factory, update)

    kwargs = update.message.reply_photo.await_args.kwargs
    assert Image.open(BytesIO(kwargs["photo"])).size == CARD_SIZE
    assert "@me" in kwargs["caption"]
    update.message.reply_text.assert_not_awaited()


async def test_the_card_has_prev_next_players_and_someone_else_buttons(session_factory) -> None:
    update = _update()
    await _run(session_factory, update)

    rows = update.message.reply_photo.await_args.kwargs["reply_markup"].inline_keyboard
    data = [[button.callback_data for button in row] for row in rows]
    # Roster: Bob (7, one game), then me (1, the viewer). Both neighbours are Bob.
    assert data == [
        [cb.card_data(1, 7, "p"), cb.card_data(1, 7, "n")],
        [cb.list_data(1, 0)],
        ["prs:1:o"],
    ]


async def test_your_own_card_in_dm_also_has_the_edit_button(session_factory) -> None:
    update = _update(chat_type=ChatType.PRIVATE, thread_id=None)
    await _run(session_factory, update)

    rows = update.message.reply_photo.await_args.kwargs["reply_markup"].inline_keyboard
    assert rows[-1][0].callback_data == cb.edit_data(1, ShowcaseKind.STAMP, 0)


async def test_someone_elses_card_in_dm_has_no_edit_button(session_factory) -> None:
    update = _update(chat_type=ChatType.PRIVATE, thread_id=None)
    await _run(session_factory, update, ["@bob"])

    rows = update.message.reply_photo.await_args.kwargs["reply_markup"].inline_keyboard
    assert all(not b.callback_data.startswith("prof:e:") for row in rows for b in row)


async def test_looking_at_someone_else_adds_the_caller_to_the_roster(session_factory) -> None:
    with session_scope(session_factory) as session:
        session.delete(session.get(Player, 1))
    update = _update()
    await _run(session_factory, update, ["@bob"])

    with session_scope(session_factory) as session:
        assert session.get(Player, 1) is not None


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


async def test_dm_profile_of_a_non_member_is_refused_and_creates_no_player(
    session_factory,
) -> None:
    with session_scope(session_factory) as session:
        session.delete(session.get(Player, 1))
    update = _update(chat_type=ChatType.PRIVATE, thread_id=None)
    await _run(session_factory, update, member=False)

    assert update.message.reply_text.await_args.args[0] == i18n.t("dm_start.not_a_member", "EN")
    update.message.reply_photo.assert_not_awaited()
    with session_scope(session_factory) as session:
        assert session.get(Player, 1) is None


async def test_dm_profile_of_a_member_by_a_non_member_is_refused(session_factory) -> None:
    update = _update(chat_type=ChatType.PRIVATE, thread_id=None)
    await _run(session_factory, update, ["@bob"], member=False)

    assert update.message.reply_text.await_args.args[0] == i18n.t("dm_start.not_a_member", "EN")
    update.message.reply_photo.assert_not_awaited()


async def test_dm_profile_of_a_member_still_gets_the_card(session_factory) -> None:
    update = _update(chat_type=ChatType.PRIVATE, thread_id=None)
    await _run(session_factory, update)

    update.message.reply_photo.assert_awaited_once()


async def test_topic_profile_does_not_check_membership(session_factory) -> None:
    update = _update()
    context = _context(session_factory, [], member=False)
    await profile_module.profile_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_photo.assert_awaited_once()
    context.bot.get_chat_member.assert_not_awaited()
