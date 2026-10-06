from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands import currency_config as currency_config_module
from nani_pix_bot.services import i18n
from nani_pix_bot.services.economy import config
from nani_pix_bot.services.economy.config import DEFAULT_AMOUNTS, EconomyKey


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
    monkeypatch.setattr(currency_config_module, "is_group_admin", AsyncMock(return_value=admin))
    update = _make_update()
    await currency_config_module.currency_config_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _make_context(session_factory, args))
    )
    return update


def _stored(session_factory) -> dict[EconomyKey, int]:
    with session_factory() as session:
        return config.get_amounts(session)


async def test_non_admin_is_refused(monkeypatch, session_factory) -> None:
    update = await _run(monkeypatch, session_factory, ["wrong_guess", "9"], admin=False)

    assert update.message.reply_text.await_args.args[0] == i18n.t("commands.admins_only", "en")
    assert _stored(session_factory) == dict(DEFAULT_AMOUNTS)


async def test_no_args_lists_every_key(monkeypatch, session_factory) -> None:
    update = await _run(monkeypatch, session_factory, [])

    reply = update.message.reply_text.await_args.args[0]
    for key in EconomyKey:
        assert key.value in reply
    assert "40" in reply


async def test_sets_a_value(monkeypatch, session_factory) -> None:
    update = await _run(monkeypatch, session_factory, ["wrong_guess", "3"])

    assert "3" in update.message.reply_text.await_args.args[0]
    assert _stored(session_factory)[EconomyKey.WRONG_GUESS] == 3


async def test_unknown_key_stores_nothing(monkeypatch, session_factory) -> None:
    update = await _run(monkeypatch, session_factory, ["nope", "3"])

    assert "nope" in update.message.reply_text.await_args.args[0]
    assert _stored(session_factory) == dict(DEFAULT_AMOUNTS)


@pytest.mark.parametrize("args", [["setter", "-1"], ["setter", "x"], ["setter"]])
async def test_bad_args_reply_usage(monkeypatch, session_factory, args) -> None:
    update = await _run(monkeypatch, session_factory, args)

    assert update.message.reply_text.await_args.args[0] == i18n.t("currency_config.usage", "en")
    assert _stored(session_factory) == dict(DEFAULT_AMOUNTS)


async def test_zero_clue_price_is_refused(monkeypatch, session_factory) -> None:
    update = await _run(monkeypatch, session_factory, ["clue_last_letter", "0"])

    assert update.message.reply_text.await_args.args[0] == i18n.t("currency_config.price_min", "en")
    assert _stored(session_factory) == dict(DEFAULT_AMOUNTS)


async def test_percent_over_100_is_refused(monkeypatch, session_factory) -> None:
    update = await _run(monkeypatch, session_factory, ["clue_cashback_percent", "150"])

    assert update.message.reply_text.await_args.args[0] == i18n.t(
        "currency_config.percent_max", "en"
    )
    assert _stored(session_factory) == dict(DEFAULT_AMOUNTS)
