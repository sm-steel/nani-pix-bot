from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from nani_pix_bot.commands import tip as tip_module
from nani_pix_bot.models import CurrencyTransfer
from nani_pix_bot.models.enums import CurrencyReason
from nani_pix_bot.models.player import Player
from nani_pix_bot.services.economy import wallet

ALICE, BOB, BOT = 1, 2, 99


def _update(*, chat_type: str = ChatType.SUPERGROUP, thread_id: int | None = 7) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = ALICE
    update.effective_user.username = "alice"
    update.effective_user.full_name = "Alice"
    update.effective_chat.id = 555 if chat_type != ChatType.PRIVATE else ALICE
    update.effective_chat.type = chat_type
    update.message.message_thread_id = thread_id
    update.effective_message = update.message
    update.message.reply_text = AsyncMock()
    return update


def _context(session_factory, args: list[str]) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    context.bot.id = BOT
    context.args = args
    return context


def _seed(session_factory) -> None:
    with session_factory() as session:
        alice = Player(telegram_user_id=ALICE, username="alice")
        bob = Player(telegram_user_id=BOB, username="bob")
        bot = Player(telegram_user_id=BOT, username="the_bot")
        session.add_all([alice, bob, bot])
        session.flush()
        wallet.credit(session, alice, 50, wallet.LedgerEntry(CurrencyReason.WIN))
        wallet.credit(session, bob, 10, wallet.LedgerEntry(CurrencyReason.WIN))
        session.commit()


def _balances(session_factory) -> tuple[int, int, int]:
    with session_factory() as session:
        rows = session.query(CurrencyTransfer).where(CurrencyTransfer.reason == CurrencyReason.TIP)
        alice, bob = session.get(Player, ALICE), session.get(Player, BOB)
        assert alice is not None
        assert bob is not None
        return alice.currency, bob.currency, rows.count()


async def _run(session_factory, args: list[str], **update_kwargs) -> MagicMock:
    update = _update(**update_kwargs)
    await tip_module.tip_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _context(session_factory, args))
    )
    return update


def _reply(update: MagicMock) -> str:
    update.message.reply_text.assert_awaited_once()
    return update.message.reply_text.await_args.args[0]


async def test_valid_tip_moves_currency_and_replies(session_factory) -> None:
    _seed(session_factory)

    update = await _run(session_factory, ["@bob", "20"])

    assert _balances(session_factory) == (30, 30, 1)
    reply = _reply(update)
    assert "20" in reply
    assert "@bob" in reply
    assert "Alice" in reply


async def test_tip_works_in_dm(session_factory) -> None:
    _seed(session_factory)

    update = await _run(session_factory, ["bob", "5"], chat_type=ChatType.PRIVATE, thread_id=None)

    assert _balances(session_factory) == (45, 15, 1)
    assert "5" in _reply(update)


async def test_tip_is_ignored_in_another_topic(session_factory) -> None:
    _seed(session_factory)

    update = await _run(session_factory, ["@bob", "5"], thread_id=8)

    update.message.reply_text.assert_not_awaited()
    assert _balances(session_factory) == (50, 10, 0)


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ([], "Usage: /tip"),
        (["@bob"], "Usage: /tip"),
        (["@bob", "lots"], "Usage: /tip"),
        (["@bob", "1.5"], "Usage: /tip"),
        (["@bob", "5", "x"], "Usage: /tip"),
        (["@unknown", "5"], "I don't know anyone called @unknown"),
        (["@the_bot", "5"], "can't take tips"),
        (["@alice", "5"], "can't tip yourself"),
        (["@bob", "0"], "at least 1"),
        (["@bob", "-4"], "at least 1"),
        (["@bob", "51"], "you have 50"),
    ],
)
async def test_refusals_move_nothing(session_factory, args: list[str], expected: str) -> None:
    _seed(session_factory)

    update = await _run(session_factory, args)

    assert expected in _reply(update)
    assert _balances(session_factory) == (50, 10, 0)
