"""/refund with no @username: the menu of players with a refundable clue
purchase, and its taps."""

from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands import refund as refund_module
from nani_pix_bot.commands import refund_picker
from nani_pix_bot.commands.helpers import player_picker
from nani_pix_bot.models import CluePurchase, CurrencyTransfer, Player
from nani_pix_bot.models.enums import ClueKind, CurrencyParty, CurrencyReason, GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.models.used_menu import UsedMenu
from nani_pix_bot.seasons.mechanics import HexKind
from nani_pix_bot.services import i18n
from tests.hex_fixtures import add_hex

ADMIN, HOST, BOB, CAROL, DAVE = 1, 9, 2, 3, 4
CANCEL = i18n.t("player_picker.cancel", "EN")
DM, MENU_ID = 1, 777


def _seed(session_factory, buyers: tuple[int, ...] = (BOB, CAROL), others=(DAVE,)) -> None:
    """Game 1 by HOST; each of `buyers` bought one last-letter clue in it, and
    `others` are known but bought nothing. CAROL guessed, so she is the most active."""
    with session_factory() as session:
        names = {HOST: "host", BOB: "bob", CAROL: "carol", DAVE: "dave"}
        ids = {HOST, *buyers, *others}
        session.add_all([Player(telegram_user_id=u, username=names.get(u, f"p{u}")) for u in ids])
        session.flush()
        session.add(Game(id=1, starter_id=HOST, status=GameStatus.UNSOLVED))
        session.flush()
        if CAROL in ids:
            session.add(GameGuess(game_id=1, player_id=CAROL, text="x", stage=1))
        for buyer in buyers:
            charge = CurrencyTransfer(
                from_type=CurrencyParty.PLAYER,
                from_player_id=buyer,
                to_type=CurrencyParty.HOUSE,
                amount=60,
                reason=CurrencyReason.CLUE_PURCHASE,
                game_id=1,
            )
            session.add(charge)
            session.flush()
            session.add(
                CluePurchase(
                    game_id=1,
                    player_id=buyer,
                    kind=ClueKind.LAST_LETTER,
                    transfer_id=charge.id,
                    created_at=datetime(2026, 10, 5, tzinfo=UTC),
                )
            )
        session.commit()


def _context(session_factory, args: list[str] | None = None) -> MagicMock:
    context = MagicMock()
    context.bot_data = {"session_factory": session_factory, "group_chat_id": 555}
    context.args = args or []
    context.bot.send_message = AsyncMock()
    return context


async def _command(monkeypatch, session_factory, args: list[str]) -> MagicMock:
    monkeypatch.setattr(refund_module, "is_group_admin", AsyncMock(return_value=True))
    update = MagicMock()
    update.effective_user.id = ADMIN
    update.effective_chat.type = "private"
    update.message.reply_text = AsyncMock()
    await refund_module.refund_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _context(session_factory, args))
    )
    return update


async def _tap(
    monkeypatch, session_factory, data: str, *, user_id: int = ADMIN, admin: bool = True
) -> MagicMock:
    monkeypatch.setattr(player_picker, "is_group_admin", AsyncMock(return_value=admin))
    update = MagicMock()
    query = update.callback_query
    query.data = data
    query.from_user.id = user_id
    query.message.chat.id = DM
    query.message.message_id = MENU_ID
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    await refund_picker.refund_picker_callback(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _context(session_factory))
    )
    return query


def _keyboard(markup) -> list[list[tuple[str, str]]]:
    return [[(b.text, b.callback_data) for b in row] for row in markup.inline_keyboard]


def _menu(update: MagicMock) -> tuple[str, list[list[tuple[str, str]]]]:
    update.message.reply_text.assert_awaited_once()
    call = update.message.reply_text.await_args
    return call.args[0], _keyboard(call.kwargs["reply_markup"])


async def test_refund_without_a_username_lists_buyers_most_active_first(
    monkeypatch, session_factory, log_records
) -> None:
    _seed(session_factory)

    text, rows = _menu(await _command(monkeypatch, session_factory, []))

    assert text == i18n.t("refund.pick_player", "EN")
    assert rows == [
        [("@carol", "rfp:1:t:3")],
        [("@bob", "rfp:1:t:2")],
        [(CANCEL, "rfp:1:x")],
    ]
    assert any(r.level == "INFO" and "opened the /refund picker" in r.message for r in log_records)


async def test_a_pick_opens_the_purchase_list_like_the_username_form(
    monkeypatch, session_factory
) -> None:
    _seed(session_factory)
    typed = await _command(monkeypatch, session_factory, ["@bob"])
    typed_text, typed_rows = _menu(typed)

    query = await _tap(monkeypatch, session_factory, "rfp:1:t:2")

    query.edit_message_text.assert_awaited_once()
    call = query.edit_message_text.await_args
    assert call.args[0] == typed_text
    assert _keyboard(call.kwargs["reply_markup"]) == typed_rows
    assert typed_rows[0][0][1].startswith("refund:s:")


async def test_a_pick_rechecks_admin_rights(monkeypatch, session_factory, log_records) -> None:
    _seed(session_factory)

    query = await _tap(monkeypatch, session_factory, "rfp:1:t:2", admin=False)

    query.answer.assert_awaited_once_with(i18n.t("commands.admins_only", "EN"), show_alert=True)
    query.edit_message_text.assert_not_awaited()
    assert any(r.level == "WARNING" and "no longer an admin" in r.message for r in log_records)


@pytest.mark.parametrize("data", ["rfp:1:t:2", "rfp:1:p:0", "rfp:1:x"])
async def test_someone_elses_tap_is_refused(monkeypatch, session_factory, data: str) -> None:
    _seed(session_factory)

    query = await _tap(monkeypatch, session_factory, data, user_id=BOB)

    query.answer.assert_awaited_once_with(i18n.t("player_picker.not_yours", "EN"), show_alert=True)
    query.edit_message_text.assert_not_awaited()


@pytest.mark.parametrize("target", [DAVE, 99])
async def test_a_pick_of_someone_with_nothing_to_refund_is_stale(
    monkeypatch, session_factory, target: int, log_records
) -> None:
    _seed(session_factory)

    query = await _tap(monkeypatch, session_factory, f"rfp:1:t:{target}")

    query.answer.assert_awaited_once_with(i18n.t("player_picker.stale", "EN"), show_alert=True)
    query.edit_message_text.assert_not_awaited()
    assert any(r.level == "WARNING" and "stale /refund pick" in r.message for r in log_records)


async def test_a_used_menu_acts_only_once(monkeypatch, session_factory, log_records) -> None:
    _seed(session_factory)
    with session_factory() as session:
        session.add(UsedMenu(chat_id=DM, message_id=MENU_ID))
        session.commit()

    query = await _tap(monkeypatch, session_factory, "rfp:1:t:2")

    query.answer.assert_awaited_once_with(i18n.t("player_picker.stale", "EN"), show_alert=True)
    query.edit_message_text.assert_not_awaited()
    assert any(r.level == "WARNING" and "used /refund menu" in r.message for r in log_records)


async def test_a_pick_claims_the_menu(monkeypatch, session_factory) -> None:
    _seed(session_factory)

    await _tap(monkeypatch, session_factory, "rfp:1:t:2")
    second = await _tap(monkeypatch, session_factory, "rfp:1:t:3")

    second.answer.assert_awaited_once_with(i18n.t("player_picker.stale", "EN"), show_alert=True)
    second.edit_message_text.assert_not_awaited()


async def test_the_menu_pages_with_nine_buyers(monkeypatch, session_factory) -> None:
    _seed(session_factory, buyers=tuple(range(10, 19)), others=())

    _, first = _menu(await _command(monkeypatch, session_factory, []))
    query = await _tap(monkeypatch, session_factory, "rfp:1:p:1")
    second = _keyboard(query.edit_message_text.await_args.kwargs["reply_markup"])

    assert len(first) == 9
    assert first[0] == [("@p10", "rfp:1:t:10")]
    assert second[0] == [("@p18", "rfp:1:t:18")]
    assert second[-1][0] == (i18n.t("player_picker.prev", "EN"), "rfp:1:p:0")


async def test_no_one_to_pick(monkeypatch, session_factory) -> None:
    _seed(session_factory, buyers=())

    update = await _command(monkeypatch, session_factory, [])

    update.message.reply_text.assert_awaited_once_with(
        i18n.t("refund.no_one", "EN"), reply_markup=None
    )


async def test_cancel(monkeypatch, session_factory) -> None:
    _seed(session_factory)

    query = await _tap(monkeypatch, session_factory, "rfp:1:x")

    query.edit_message_text.assert_awaited_once_with(
        i18n.t("player_picker.cancelled", "EN"), reply_markup=None
    )


async def test_extra_args_still_show_the_usage(monkeypatch, session_factory) -> None:
    _seed(session_factory)

    update = await _command(monkeypatch, session_factory, ["@bob", "@carol"])

    update.message.reply_text.assert_awaited_once_with(i18n.t("refund.usage", "EN"))


async def test_a_faceless_buyer_shows_unmasked_in_the_menu(
    monkeypatch, session_factory, fake_runs
) -> None:
    _seed(session_factory)
    with session_factory() as session:
        add_hex(session, BOB, HexKind.NOPPERABO, hours=2)
        session.commit()

    _, rows = _menu(await _command(monkeypatch, session_factory, []))

    assert [row[0][0] for row in rows[:-1]] == ["@carol", "@bob"]
