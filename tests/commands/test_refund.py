from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from telegram import Update
from telegram.error import Forbidden
from telegram.ext import ContextTypes

from nani_pix_bot.commands import refund as refund_module
from nani_pix_bot.models import CluePurchase, CurrencyTransfer, Player
from nani_pix_bot.models.enums import ClueKind, CurrencyParty, CurrencyReason, GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import i18n
from tests.services.economy.ledger import ledger_balance

ADMIN, BUYER, GROUP = 1, 2, 555


def _seed(session_factory, *, purchases: int = 1) -> list[int]:
    """BUYER (@buyer) bought `purchases` last-letter clues in game 1, each 60 💠."""
    with session_factory() as session:
        session.add_all(
            [
                Player(telegram_user_id=9),
                Player(telegram_user_id=BUYER, username="buyer", currency=0),
            ]
        )
        session.flush()
        session.add(Game(id=1, starter_id=9, status=GameStatus.UNSOLVED))
        session.flush()
        ids = []
        for n in range(purchases):
            charge = CurrencyTransfer(
                from_type=CurrencyParty.PLAYER,
                from_player_id=BUYER,
                to_type=CurrencyParty.HOUSE,
                amount=60,
                reason=CurrencyReason.CLUE_PURCHASE,
                game_id=1,
            )
            session.add(charge)
            session.flush()
            row = CluePurchase(
                game_id=1,
                player_id=BUYER,
                kind=ClueKind.LAST_LETTER,
                transfer_id=charge.id,
                created_at=datetime.now(UTC) + timedelta(seconds=n),
            )
            session.add(row)
            session.flush()
            ids.append(row.id)
        # ledger: BUYER started with 60 * purchases and spent it all
        session.add(
            CurrencyTransfer(
                from_type=CurrencyParty.HOUSE,
                to_type=CurrencyParty.PLAYER,
                to_player_id=BUYER,
                amount=60 * purchases,
                reason=CurrencyReason.GRANT,
            )
        )
        session.commit()
        return ids


def _context(session_factory, args=None) -> MagicMock:
    context = MagicMock()
    context.bot_data = {"session_factory": session_factory, "group_chat_id": GROUP}
    context.args = args or []
    context.bot.send_message = AsyncMock()
    return context


def _command_update() -> MagicMock:
    update = MagicMock()
    update.effective_user.id = ADMIN
    update.effective_chat.type = "private"
    update.message.reply_text = AsyncMock()
    return update


def _callback_update(data: str) -> MagicMock:
    update = MagicMock()
    update.effective_chat.type = "private"
    update.callback_query.data = data
    update.callback_query.from_user.id = ADMIN
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


async def _command(monkeypatch, session_factory, args, *, admin=True):
    monkeypatch.setattr(refund_module, "is_group_admin", AsyncMock(return_value=admin))
    update, context = _command_update(), _context(session_factory, args)
    await refund_module.refund_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )
    return update, context


async def _tap(monkeypatch, session_factory, data, *, admin=True):
    monkeypatch.setattr(refund_module, "is_group_admin", AsyncMock(return_value=admin))
    update, context = _callback_update(data), _context(session_factory)
    await refund_module.refund_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )
    return update, context


async def test_non_admin_is_refused(monkeypatch, session_factory) -> None:
    _seed(session_factory)
    update, _ = await _command(monkeypatch, session_factory, ["@buyer"], admin=False)
    assert update.message.reply_text.await_args.args[0] == i18n.t("commands.admins_only", "en")


async def test_lists_purchases_five_per_page(monkeypatch, session_factory) -> None:
    ids = _seed(session_factory, purchases=7)
    update, _ = await _command(monkeypatch, session_factory, ["@buyer"])

    markup = update.message.reply_text.await_args.kwargs["reply_markup"]
    item_rows = [
        row for row in markup.inline_keyboard if row[0].callback_data.startswith("refund:s:")
    ]
    assert [row[0].callback_data for row in item_rows] == [f"refund:s:{i}" for i in reversed(ids)][
        :5
    ]
    nav = markup.inline_keyboard[-1]
    assert [b.callback_data for b in nav] == [f"refund:p:{BUYER}:1"]
    assert "#1 · last letter · 60 💠" in item_rows[0][0].text


async def test_confirm_refunds_and_dms_the_player(monkeypatch, session_factory) -> None:
    [purchase_id] = _seed(session_factory)
    update, context = await _tap(monkeypatch, session_factory, f"refund:y:{purchase_id}")

    with session_factory() as session:
        assert session.get(CluePurchase, purchase_id) is None
        assert session.get(Player, BUYER).currency == 60
        assert ledger_balance(session, BUYER) == 60
    assert context.bot.send_message.await_args.kwargs["chat_id"] == BUYER
    assert "60" in context.bot.send_message.await_args.kwargs["text"]
    assert update.callback_query.edit_message_text.await_args.args[0] == i18n.t(
        "refund.done", "en", amount=60, username="buyer"
    )


async def test_blocked_dm_still_refunds(monkeypatch, session_factory) -> None:
    [purchase_id] = _seed(session_factory)
    monkeypatch.setattr(refund_module, "is_group_admin", AsyncMock(return_value=True))
    update, context = _callback_update(f"refund:y:{purchase_id}"), _context(session_factory)
    context.bot.send_message = AsyncMock(side_effect=Forbidden("blocked"))

    await refund_module.refund_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        assert session.get(Player, BUYER).currency == 60


async def test_second_confirm_is_stale_and_credits_nothing(monkeypatch, session_factory) -> None:
    [purchase_id] = _seed(session_factory)
    await _tap(monkeypatch, session_factory, f"refund:y:{purchase_id}")
    update, context = await _tap(monkeypatch, session_factory, f"refund:y:{purchase_id}")

    with session_factory() as session:
        assert session.get(Player, BUYER).currency == 60
    assert update.callback_query.edit_message_text.await_args.args[0] == i18n.t(
        "refund.stale", "en"
    )
    context.bot.send_message.assert_not_awaited()


async def test_select_of_refunded_purchase_is_stale(monkeypatch, session_factory) -> None:
    [purchase_id] = _seed(session_factory)
    await _tap(monkeypatch, session_factory, f"refund:y:{purchase_id}")
    update, _ = await _tap(monkeypatch, session_factory, f"refund:s:{purchase_id}")
    assert update.callback_query.edit_message_text.await_args.args[0] == i18n.t(
        "refund.stale", "en"
    )
