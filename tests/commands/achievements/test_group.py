from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes

from nani_pix_bot.commands import achievements
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import i18n
from nani_pix_bot.services.achievements import engine
from tests.conftest import LogLine

pytestmark = pytest.mark.achievements


def _update(user_id: int = 1, thread_id: int | None = 7) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.id = 555
    update.effective_chat.type = "supergroup"
    update.message.message_thread_id = thread_id
    update.message.chat_id = 555
    update.effective_message = update.message
    update.message.reply_text = AsyncMock()
    return update


def _context(session_factory, args: list[str]) -> MagicMock:
    context = MagicMock()
    context.args = args
    context.bot.get_chat_member = AsyncMock(return_value=MagicMock(status=ChatMemberStatus.MEMBER))
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
        "bot_username": "nanibot",
    }
    return context


async def _run(update: MagicMock, context: MagicMock) -> AsyncMock:
    with patch.object(achievements, "send_rich", new=AsyncMock()) as sent:
        await achievements.achievements_command(
            cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
        )
    return sent


async def test_summary_of_a_named_player_with_a_dm_link(session_factory) -> None:
    with session_scope(session_factory) as session:
        session.add_all([Player(telegram_user_id=1), Player(telegram_user_id=2, username="b_ob")])
        session.flush()
        engine.grant(session, engine.GrantRequest(2, "clutch"))

    sent = await _run(_update(), _context(session_factory, ["@b_ob"]))

    _bot, target, markdown, markup = sent.await_args_list[0].args
    assert (target.chat_id, target.thread_id) == (555, 7)
    assert r"@b\_ob" in markdown  # escaped
    assert "Clutch" in markdown
    assert markup.inline_keyboard[0][0].url == "https://t.me/nanibot?start=ach_2"


async def test_unknown_player_gets_a_plain_reply(session_factory) -> None:
    update = _update()

    sent = await _run(update, _context(session_factory, ["@nobody"]))

    sent.assert_not_awaited()
    update.message.reply_text.assert_awaited_once()


async def test_top_lists_players_by_points(session_factory) -> None:
    with session_scope(session_factory) as session:
        session.add(Player(telegram_user_id=1, username="alice"))
        session.flush()
        engine.grant(session, engine.GrantRequest(1, "clutch"))

    sent = await _run(_update(), _context(session_factory, ["top"]))

    markdown = sent.await_args_list[0].args[2]
    assert "| 1 | @alice | 1 | 8 |" in markdown


async def test_ignored_outside_the_game_topic(session_factory) -> None:
    sent = await _run(_update(thread_id=99), _context(session_factory, []))

    sent.assert_not_awaited()


async def test_in_dm_a_named_player_opens_the_browser(session_factory) -> None:
    with session_scope(session_factory) as session:
        session.add_all([Player(telegram_user_id=1), Player(telegram_user_id=2, username="bob")])
    update = _update()
    update.effective_chat.type = "private"

    with patch.object(achievements.browser, "open_browser", new=AsyncMock()) as opened:
        await achievements.achievements_command(
            cast(Update, update),
            cast(ContextTypes.DEFAULT_TYPE, _context(session_factory, ["@bob"])),
        )

    assert opened.await_args_list[0].args[2] == achievements.browser.Browse(1, 2)


async def test_in_dm_an_unknown_player_gets_the_unknown_reply(session_factory) -> None:
    with session_scope(session_factory) as session:
        session.add(Player(telegram_user_id=1))
    update = _update()
    update.effective_chat.type = "private"

    with patch.object(achievements.browser, "open_browser", new=AsyncMock()) as opened:
        await achievements.achievements_command(
            cast(Update, update),
            cast(ContextTypes.DEFAULT_TYPE, _context(session_factory, ["@nobody"])),
        )

    opened.assert_not_awaited()
    assert "@nobody" in update.message.reply_text.await_args_list[0].args[0]


def _outsider(context: MagicMock) -> MagicMock:
    context.bot.get_chat_member = AsyncMock(return_value=MagicMock(status=ChatMemberStatus.LEFT))
    return context


async def test_in_dm_a_non_member_is_refused_and_no_browser_opens(
    session_factory, log_records: list[LogLine]
) -> None:
    with session_scope(session_factory) as session:
        session.add(Player(telegram_user_id=2, username="bob"))
    update = _update(user_id=9)
    update.effective_chat.type = "private"

    with patch.object(achievements.browser, "open_browser", new=AsyncMock()) as opened:
        await achievements.achievements_command(
            cast(Update, update),
            cast(ContextTypes.DEFAULT_TYPE, _outsider(_context(session_factory, ["@bob"]))),
        )

    opened.assert_not_awaited()
    reply = update.message.reply_text.await_args_list[0].args[0]
    assert reply == i18n.t("dm_start.not_a_member", "en")
    assert any(r.level == "WARNING" and "not a group member" in r.message for r in log_records)


async def test_in_dm_top_opens_the_top_view(session_factory) -> None:
    with session_scope(session_factory) as session:
        session.add(Player(telegram_user_id=1, username="alice"))
        session.flush()
        engine.grant(session, engine.GrantRequest(1, "clutch"))
    update = _update()
    update.effective_chat.type = "private"
    update.message.chat_id = 1

    with (
        patch.object(achievements.browser, "open_browser", new=AsyncMock()) as opened,
        patch.object(achievements.browser, "send_rich", new=AsyncMock()) as sent,
    ):
        await achievements.achievements_command(
            cast(Update, update),
            cast(ContextTypes.DEFAULT_TYPE, _context(session_factory, ["top"])),
        )

    opened.assert_not_awaited()
    update.message.reply_text.assert_not_awaited()  # not "unknown player top"
    _bot, target, markdown, _markup = sent.await_args_list[0].args
    assert target.chat_id == 1
    assert "| 1 | @alice | 1 | 8 |" in markdown
