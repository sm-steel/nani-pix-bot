from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.achievements import browser, compare
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.player import Player
from nani_pix_bot.services.achievements import engine
from nani_pix_bot.services.achievements.status import CompareFilter

pytestmark = pytest.mark.achievements


def _seed(session_factory) -> None:
    with session_scope(session_factory) as session:
        session.add_all([Player(telegram_user_id=1), Player(telegram_user_id=2, username="bob")])
        session.flush()
        engine.grant(session, engine.GrantRequest(2, "clutch"))
        engine.grant(session, engine.GrantRequest(1, "sharpshooter", 1))


def _render(session_factory, **kwargs) -> tuple[str, list]:
    with session_scope(session_factory) as session:
        request = compare.CompareRequest(viewer_id=1, other_id=2, **kwargs)
        markdown, markup = compare.compare_view(session, request, "EN")
    return markdown, [b.callback_data for row in markup.inline_keyboard for b in row]


def test_compare_table_marks_each_side(session_factory) -> None:
    _seed(session_factory)

    markdown, buttons = _render(session_factory)
    assert "| Achievement | You | @bob |" in markdown
    assert "| Sharpshooter | ✅ I | ⬜ |" in markdown
    assert compare.compare_data(2, CompareFilter.THEIRS, 0) in buttons
    assert compare.compare_data(2, CompareFilter.ALL, 1) in buttons  # next page
    assert browser.view_data(2, browser.View.ALL, 0) in buttons  # back to their list

    theirs, _ = _render(session_factory, filt=CompareFilter.THEIRS)
    assert "| Clutch | ⬜ | ✅ |" in theirs
    assert "Sharpshooter" not in theirs


def test_a_hidden_achievement_stays_unknown_until_someone_earns_it(session_factory) -> None:
    _seed(session_factory)
    markdown, _ = _render(session_factory, page=3)
    assert "| ❔ | ⬜ | ⬜ |" in markdown

    with session_scope(session_factory) as session:
        engine.grant(session, engine.GrantRequest(2, "so_close"))
    markdown, _ = _render(session_factory, page=3)
    assert "| So Close | ⬜ | ✅ |" in markdown


def test_the_compare_action_is_registered() -> None:
    assert "c" in browser.ACTIONS


async def test_a_compare_tap_edits_in_the_table(session_factory) -> None:
    _seed(session_factory)
    update = MagicMock()
    update.callback_query.data = "ach:c:2:t:0"
    update.callback_query.from_user.id = 1
    update.callback_query.answer = AsyncMock()
    update.callback_query.message.chat.id = 1
    update.callback_query.message.message_id = 42
    context = MagicMock()
    context.bot_data = {"session_factory": session_factory}

    with patch.object(browser, "edit_rich", new=AsyncMock()) as edited:
        await browser.achievements_callback(
            cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
        )

    args = edited.await_args_list[0].args
    assert args[1].message_id == 42
    assert "| Clutch | ⬜ | ✅ |" in args[2]
    assert "Sharpshooter" not in args[2]  # "only they have"


async def test_an_unknown_filter_and_huge_page_fall_back_to_all_on_the_last_page(
    session_factory,
) -> None:
    _seed(session_factory)
    update = MagicMock()
    update.callback_query.data = "ach:c:2:x:999"
    update.callback_query.from_user.id = 1
    update.callback_query.answer = AsyncMock()
    update.callback_query.message.chat.id = 1
    update.callback_query.message.message_id = 42
    context = MagicMock()
    context.bot_data = {"session_factory": session_factory}

    with patch.object(browser, "edit_rich", new=AsyncMock()) as edited:
        await browser.achievements_callback(
            cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
        )

    args = edited.await_args_list[0].args
    assert "Sharpshooter" not in args[2]  # first page is not shown
    assert "| Achievement | You | @bob |" in args[2]
    buttons = [b.callback_data for row in args[3].inline_keyboard for b in row]
    assert compare.compare_data(2, CompareFilter.ALL, 2) in buttons  # prev of the last page (3)
    assert "ach:c:2:a:3" not in buttons
