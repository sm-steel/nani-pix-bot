from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands import partialmatch as partialmatch_module
from nani_pix_bot.services import i18n, settings


def _make_update() -> MagicMock:
    update = MagicMock()
    update.effective_user.id = 1
    update.effective_chat.type = "private"
    update.message.reply_text = AsyncMock()
    return update


def _make_context(session_factory, args: list[str]) -> MagicMock:
    context = MagicMock()
    context.bot_data = {"session_factory": session_factory, "group_chat_id": 555}
    context.args = args
    return context


async def _run(monkeypatch, session_factory, args: list[str], *, admin: bool = True) -> MagicMock:
    monkeypatch.setattr(partialmatch_module, "is_group_admin", AsyncMock(return_value=admin))
    update = _make_update()
    await partialmatch_module.partialmatch_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _make_context(session_factory, args))
    )
    return update


def _stored(session_factory) -> int:
    with session_factory() as session:
        return settings.get_partial_match_min_letters(session)


async def test_non_admin_is_refused(monkeypatch, session_factory) -> None:
    update = await _run(monkeypatch, session_factory, ["6"], admin=False)

    assert update.message.reply_text.await_args.args[0] == i18n.t("commands.admins_only", "en")
    assert _stored(session_factory) == 4


async def test_no_args_shows_the_current_value(monkeypatch, session_factory) -> None:
    update = await _run(monkeypatch, session_factory, [])

    assert update.message.reply_text.await_args.args[0] == i18n.t(
        "partialmatch.current", "en", value=4
    )


async def test_sets_a_value(monkeypatch, session_factory) -> None:
    update = await _run(monkeypatch, session_factory, ["6"])

    assert update.message.reply_text.await_args.args[0] == i18n.t(
        "partialmatch.updated", "en", value=6
    )
    assert _stored(session_factory) == 6


@pytest.mark.parametrize("args", [["-1"], ["x"], ["1", "2"]])
async def test_bad_args_reply_usage(monkeypatch, session_factory, args) -> None:
    update = await _run(monkeypatch, session_factory, args)

    assert update.message.reply_text.await_args.args[0] == i18n.t("partialmatch.usage", "en")
    assert _stored(session_factory) == 4
