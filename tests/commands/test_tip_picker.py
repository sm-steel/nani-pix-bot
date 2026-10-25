"""/tip with no @username: the player picker, its amount buttons, and its taps."""

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
from nani_pix_bot.seasons.mechanics import HexKind
from nani_pix_bot.services import i18n
from nani_pix_bot.services.economy import wallet
from nani_pix_bot.services.seasons import hex_marks
from tests.hex_fixtures import add_hex

ALICE, BOB, CAROL, BOT = 1, 2, 3, 99


def _seed(session_factory, *, alice_balance: int = 50, extra: tuple[int, ...] = ()) -> None:
    """Alice (the tipper) holds `alice_balance`; Bob has a username, Carol only a
    first name; the bot has its own row."""
    with session_factory() as session:
        alice = Player(telegram_user_id=ALICE, username="alice")
        session.add_all(
            [
                alice,
                Player(telegram_user_id=BOB, username="bob"),
                Player(telegram_user_id=CAROL, first_name="Carol"),
                Player(telegram_user_id=BOT, username="the_bot"),
                *[Player(telegram_user_id=u, username=f"p{u}") for u in extra],
            ]
        )
        session.flush()
        if alice_balance:
            wallet.credit(session, alice, alice_balance, wallet.LedgerEntry(CurrencyReason.WIN))
        session.commit()


def _context(session_factory, args: list[str] | None = None) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    context.bot.id = BOT
    context.args = args or []
    return context


def _update() -> MagicMock:
    update = MagicMock()
    update.effective_user.id = ALICE
    update.effective_user.username = "alice"
    update.effective_user.full_name = "Alice"
    update.effective_chat.id = ALICE
    update.effective_chat.type = ChatType.PRIVATE
    update.effective_message = update.message
    update.message.reply_text = AsyncMock()
    return update


async def _tip(session_factory, args: list[str]) -> MagicMock:
    update = _update()
    await tip_module.tip_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _context(session_factory, args))
    )
    return update


async def _tap(session_factory, data: str, user_id: int = ALICE) -> MagicMock:
    update = MagicMock()
    query = update.callback_query
    query.data = data
    query.from_user.id = user_id
    query.from_user.username = "alice" if user_id == ALICE else None
    query.from_user.full_name = "Alice" if user_id == ALICE else "Someone"
    query.message.chat.type = ChatType.PRIVATE
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    await tip_module.tip_callback(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _context(session_factory))
    )
    return query


def _keyboard(markup) -> list[list[tuple[str, str]]]:
    return [[(b.text, b.callback_data) for b in row] for row in markup.inline_keyboard]


def _picker(update: MagicMock) -> tuple[str, list[list[tuple[str, str]]]]:
    update.message.reply_text.assert_awaited_once()
    call = update.message.reply_text.await_args
    return call.args[0], _keyboard(call.kwargs["reply_markup"])


def _edited(query: MagicMock) -> str:
    query.edit_message_text.assert_awaited_once()
    return query.edit_message_text.await_args.args[0]


def _balances(session_factory) -> tuple[int, int, int]:
    with session_factory() as session:
        rows = session.query(CurrencyTransfer).where(CurrencyTransfer.reason == CurrencyReason.TIP)
        alice, bob = session.get(Player, ALICE), session.get(Player, BOB)
        assert alice is not None
        assert bob is not None
        return alice.currency, bob.currency, rows.count()


CANCEL = i18n.t("player_picker.cancel", "EN")


async def test_tip_alone_lists_everyone_but_the_tipper_and_the_bot(
    session_factory, log_records
) -> None:
    _seed(session_factory)

    text, rows = _picker(await _tip(session_factory, []))

    assert text == i18n.t("tip.pick_player", "EN")
    assert rows == [[("@bob", "tip:1:0:t:2")], [("Carol", "tip:1:0:t:3")], [(CANCEL, "tip:1:0:x")]]
    assert any(r.level == "INFO" and "opened the /tip picker" in r.message for r in log_records)


async def test_tip_with_an_amount_carries_it_in_the_picker(session_factory) -> None:
    _seed(session_factory)

    text, rows = _picker(await _tip(session_factory, ["20"]))

    assert text == i18n.t("tip.pick_player_amount", "EN", amount=20)
    assert rows[0] == [("@bob", "tip:1:20:t:2")]
    assert rows[-1] == [(CANCEL, "tip:1:20:x")]


@pytest.mark.parametrize(("args", "expected"), [(["0"], "at least 1"), (["51"], "you have 50")])
async def test_an_amount_that_cant_be_tipped_is_refused_before_the_picker(
    session_factory, args: list[str], expected: str
) -> None:
    _seed(session_factory)

    update = await _tip(session_factory, args)

    assert expected in update.message.reply_text.await_args.args[0]
    assert update.message.reply_text.await_args.kwargs.get("reply_markup") is None


async def test_a_pick_with_an_amount_tips_like_the_username_form(session_factory) -> None:
    _seed(session_factory)

    query = await _tap(session_factory, "tip:1:20:t:2")

    assert _balances(session_factory) == (30, 20, 1)
    assert _edited(query) == i18n.t("tip.done", "EN", sender="Alice", recipient="@bob", amount=20)


async def test_the_username_form_gives_the_same_reply(session_factory) -> None:
    _seed(session_factory)

    update = await _tip(session_factory, ["@bob", "20"])

    assert update.message.reply_text.await_args.args[0] == i18n.t(
        "tip.done", "EN", sender="Alice", recipient="@bob", amount=20
    )


async def test_a_player_without_a_username_is_named_by_first_name(session_factory) -> None:
    _seed(session_factory)

    query = await _tap(session_factory, "tip:1:5:t:3")

    assert _edited(query) == i18n.t("tip.done", "EN", sender="Alice", recipient="Carol", amount=5)


async def test_a_pick_without_an_amount_shows_the_affordable_amounts(session_factory) -> None:
    _seed(session_factory)

    query = await _tap(session_factory, "tip:1:0:t:2")

    assert _edited(query) == i18n.t("tip.pick_amount", "EN", recipient="@bob")
    rows = _keyboard(query.edit_message_text.await_args.kwargs["reply_markup"])
    assert rows == [
        [("10 💠", "tip:1:10:t:2"), ("25 💠", "tip:1:25:t:2"), ("50 💠", "tip:1:50:t:2")],
        [(CANCEL, "tip:1:0:x")],
    ]
    assert _balances(session_factory) == (50, 0, 0)


async def test_no_affordable_amount_gives_the_insufficient_reply(session_factory) -> None:
    _seed(session_factory, alice_balance=5)

    query = await _tap(session_factory, "tip:1:0:t:2")

    assert _edited(query) == i18n.t("tip.refusal.insufficient", "EN", minimum=1, balance=5)


async def test_an_amount_button_tips(session_factory) -> None:
    _seed(session_factory)

    query = await _tap(session_factory, "tip:1:25:t:2")

    assert _balances(session_factory) == (25, 25, 1)
    assert "25" in _edited(query)


async def test_a_tap_that_can_no_longer_be_afforded_is_refused(session_factory) -> None:
    _seed(session_factory)

    query = await _tap(session_factory, "tip:1:100:t:2")

    assert _edited(query) == i18n.t("tip.refusal.insufficient", "EN", minimum=1, balance=50)
    assert _balances(session_factory) == (50, 0, 0)


@pytest.mark.parametrize("data", ["tip:1:20:t:2", "tip:1:0:t:2", "tip:1:0:p:0", "tip:1:0:x"])
async def test_someone_elses_tap_is_refused(session_factory, data: str, log_records) -> None:
    _seed(session_factory)

    query = await _tap(session_factory, data, user_id=BOB)

    query.answer.assert_awaited_once_with(i18n.t("player_picker.not_yours", "EN"), show_alert=True)
    query.edit_message_text.assert_not_awaited()
    assert _balances(session_factory) == (50, 0, 0)
    assert any(r.level == "WARNING" and "someone else's" in r.message for r in log_records)


async def test_the_picker_pages_with_nine_candidates(session_factory) -> None:
    _seed(session_factory, extra=tuple(range(10, 17)))  # Bob, Carol and seven more

    _, first = _picker(await _tip(session_factory, []))
    query = await _tap(session_factory, "tip:1:0:p:1")
    second = _keyboard(query.edit_message_text.await_args.kwargs["reply_markup"])

    assert len(first) == 9
    assert first[-1] == [(CANCEL, "tip:1:0:x"), (i18n.t("player_picker.next", "EN"), "tip:1:0:p:1")]
    assert second == [
        [("@p16", "tip:1:0:t:16")],
        [(i18n.t("player_picker.prev", "EN"), "tip:1:0:p:0"), (CANCEL, "tip:1:0:x")],
    ]


async def test_no_one_to_tip(session_factory) -> None:
    with session_factory() as session:
        session.add_all([Player(telegram_user_id=ALICE), Player(telegram_user_id=BOT)])
        session.commit()

    update = await _tip(session_factory, [])

    assert update.message.reply_text.await_args.args[0] == i18n.t("tip.no_one", "EN")
    assert update.message.reply_text.await_args.kwargs.get("reply_markup") is None


async def test_cancel(session_factory) -> None:
    _seed(session_factory)

    query = await _tap(session_factory, "tip:1:0:x")

    assert _edited(query) == i18n.t("player_picker.cancelled", "EN")


async def test_a_faceless_player_shows_unmasked_in_the_picker_only(
    session_factory, fake_runs
) -> None:
    _seed(session_factory)
    with session_factory() as session:
        add_hex(session, BOB, HexKind.NOPPERABO, hours=2)
        session.commit()

    _, rows = _picker(await _tip(session_factory, []))
    query = await _tap(session_factory, "tip:1:5:t:2")

    assert rows[0] == [("@bob", "tip:1:0:t:2")]
    assert "@bob" not in _edited(query)
    assert hex_marks.FACELESS in _edited(query)


@pytest.mark.parametrize("data", ["tip:1:x:t:2", "tip:1:0:q", "tip:1", "tip:1:0:t:z"])
async def test_a_malformed_tap_is_only_answered(session_factory, data: str) -> None:
    _seed(session_factory)

    query = await _tap(session_factory, data)

    query.answer.assert_awaited_once_with()
    query.edit_message_text.assert_not_awaited()
    assert _balances(session_factory) == (50, 0, 0)
