from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes

from nani_pix_bot.commands import title as title_module
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import i18n
from nani_pix_bot.services.achievements import engine
from tests.conftest import LogLine

pytestmark = pytest.mark.achievements

ME, OTHER = 1, 2


def _seed(session_factory) -> tuple[int, int]:
    """ME holds a champion grant (a title); OTHER holds one too. Returns their grant ids."""
    with session_factory() as session:
        session.add_all([Player(telegram_user_id=ME), Player(telegram_user_id=OTHER)])
        session.flush()
        mine = engine.grant(session, engine.GrantRequest(ME, "champion_month", 1, "2026-10"))
        theirs = engine.grant(session, engine.GrantRequest(OTHER, "champion_week", 1, "2026-W41"))
        session.commit()
        return mine.id, theirs.id


def _context(session_factory, *, member: bool = True) -> MagicMock:
    context = MagicMock()
    context.bot_data = {"session_factory": session_factory, "group_chat_id": 555}
    joined = ChatMemberStatus.MEMBER if member else ChatMemberStatus.LEFT
    context.bot.get_chat_member = AsyncMock(return_value=MagicMock(status=joined))
    return context


def _command_update() -> MagicMock:
    update = MagicMock()
    update.effective_user.id = ME
    update.effective_chat.type = "private"
    update.message.reply_text = AsyncMock()
    return update


def _callback_update(data: str) -> MagicMock:
    update = MagicMock()
    update.effective_chat.type = "private"
    update.callback_query.data = data
    update.callback_query.from_user.id = ME
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


async def _command(session_factory) -> MagicMock:
    update = _command_update()
    await title_module.title_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _context(session_factory))
    )
    return update


async def _tap(session_factory, data: str) -> MagicMock:
    update = _callback_update(data)
    await title_module.title_callback(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _context(session_factory))
    )
    return update


def _edited(update: MagicMock) -> str:
    return update.callback_query.edit_message_text.await_args.args[0]


async def test_no_titles_says_none_earned(session_factory) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=ME))
        session.commit()

    update = await _command(session_factory)

    assert update.message.reply_text.await_args.args[0] == i18n.t("title.none_earned", "en")


async def test_one_eligible_title_lists_it_and_the_clear_button(session_factory) -> None:
    mine, _ = _seed(session_factory)

    update = await _command(session_factory)

    markup = update.message.reply_text.await_args.kwargs["reply_markup"]
    assert [row[0].callback_data for row in markup.inline_keyboard] == [
        f"title:{mine}",
        "title:none",
    ]
    assert markup.inline_keyboard[0][0].text == "Champion of October 2026"


async def test_choosing_a_title_stores_it(session_factory) -> None:
    mine, _ = _seed(session_factory)

    update = await _tap(session_factory, f"title:{mine}")

    with session_factory() as session:
        assert session.get(Player, ME).title_key == "champion_month:1:2026-10"
    assert _edited(update) == i18n.t("title.set", "en", title="Champion of October 2026")


async def test_someone_elses_grant_is_stale(session_factory, log_records: list[LogLine]) -> None:
    _, theirs = _seed(session_factory)

    update = await _tap(session_factory, f"title:{theirs}")

    with session_factory() as session:
        assert session.get(Player, ME).title_key is None
    assert _edited(update) == i18n.t("title.stale", "en")
    assert any(r.level == "WARNING" for r in log_records)


@pytest.mark.parametrize("data", ["title:abc", f"title:{2**63}"])
async def test_a_forged_id_is_stale(session_factory, log_records: list[LogLine], data) -> None:
    _seed(session_factory)

    update = await _tap(session_factory, data)

    assert _edited(update) == i18n.t("title.stale", "en")
    assert any(r.level == "WARNING" for r in log_records)


async def test_none_clears_the_title(session_factory) -> None:
    mine, _ = _seed(session_factory)
    await _tap(session_factory, f"title:{mine}")

    update = await _tap(session_factory, "title:none")

    with session_factory() as session:
        assert session.get(Player, ME).title_key is None
    assert _edited(update) == i18n.t("title.cleared", "en")


async def test_a_non_member_is_refused(session_factory, log_records: list[LogLine]) -> None:
    _seed(session_factory)
    update = _command_update()

    await title_module.title_command(
        cast(Update, update),
        cast(ContextTypes.DEFAULT_TYPE, _context(session_factory, member=False)),
    )

    update.message.reply_text.assert_awaited_once_with(i18n.t("dm_start.not_a_member", "en"))
    assert any(r.level == "WARNING" and "not a group member" in r.message for r in log_records)
