from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.achievements import browser
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.player import Player
from nani_pix_bot.services.achievements import engine
from nani_pix_bot.services.achievements.status import View

pytestmark = pytest.mark.achievements


def _context(session_factory) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    return context


def _seed(session_factory) -> None:
    with session_scope(session_factory) as session:
        session.add_all([Player(telegram_user_id=1), Player(telegram_user_id=2, username="bob")])
        session.flush()
        engine.grant(session, engine.GrantRequest(2, "clutch"))


def test_callback_data_round_trips_and_rejects_forgeries() -> None:
    data = browser.view_data(2, View.NOT_YET, 3)
    assert data == "ach:v:2:n:3"
    assert browser.parse(data) == ("v", [2, "n", 3])
    assert browser.parse("ach:v:2:n:²") is None
    assert browser.parse("ach:v:2") is None
    assert browser.parse("ach:zz:1") is None


async def test_opening_someone_elses_list_offers_compare_and_top(session_factory) -> None:
    _seed(session_factory)
    message = MagicMock()
    message.chat_id = 1

    with patch.object(browser, "send_rich", new=AsyncMock()) as sent:
        await browser.open_browser(message, _context(session_factory), browser.Browse(1, 2))

    _bot, target, markdown, markup = sent.await_args_list[0].args
    assert target.chat_id == 1
    assert "Clutch" in markdown
    buttons = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "ach:v:2:e:0" in buttons
    assert "ach:c:2:a:0" in buttons
    assert "ach:t:0" in buttons


async def test_own_list_has_no_compare_button(session_factory) -> None:
    _seed(session_factory)
    message = MagicMock()
    message.chat_id = 2

    with patch.object(browser, "send_rich", new=AsyncMock()) as sent:
        await browser.open_browser(message, _context(session_factory), browser.Browse(2, 2))

    buttons = [
        b.callback_data for row in sent.await_args_list[0].args[3].inline_keyboard for b in row
    ]
    assert not any(b.startswith("ach:c:") for b in buttons)


async def test_a_tab_tap_edits_the_message_in_place(session_factory) -> None:
    _seed(session_factory)
    update = MagicMock()
    update.callback_query.data = "ach:v:2:n:0"
    update.callback_query.from_user.id = 1
    update.callback_query.answer = AsyncMock()
    update.callback_query.message.chat.id = 1
    update.callback_query.message.message_id = 42

    with patch.object(browser, "edit_rich", new=AsyncMock()) as edited:
        await browser.achievements_callback(
            cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _context(session_factory))
        )

    target, markdown = edited.await_args_list[0].args[1], edited.await_args_list[0].args[2]
    assert target.message_id == 42
    assert "Clutch" not in markdown  # Not yet: earned rows are gone


async def test_a_forged_tap_is_ignored(session_factory, records) -> None:
    update = MagicMock()
    update.callback_query.data = "ach:v:x:y:z"
    update.callback_query.answer = AsyncMock()

    with patch.object(browser, "edit_rich", new=AsyncMock()) as edited:
        await browser.achievements_callback(
            cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _context(session_factory))
        )

    edited.assert_not_awaited()
    assert any(level == "WARNING" for level, _ in records)


def test_the_last_page_clamps() -> None:
    assert browser.clamp_page(99, total=20) == 2
    assert browser.clamp_page(-1, total=20) == 0
    assert browser.clamp_page(0, total=0) == 0
