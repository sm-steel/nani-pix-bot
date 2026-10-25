"""/grantitem <item id> with no @username: the menu of every known player,
and its taps. The buttons carry a digest of the item id, not the id."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes

from nani_pix_bot.commands import grant_item as grant_item_module
from nani_pix_bot.commands import grant_item_picker
from nani_pix_bot.models.cosmetics import OwnedItem
from nani_pix_bot.models.enums import ItemSource
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.models.used_menu import UsedMenu
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.mechanics import HexKind
from nani_pix_bot.services import i18n, players
from tests.hex_fixtures import add_hex

ADMIN, BOB, CAROL, BOT = 1, 2, 3, 100
ITEM_ID = "demo_1/name_flair.blossom"
DIGEST = grant_item_picker.item_digest(ITEM_ID)
CANCEL = i18n.t("player_picker.cancel", "EN")
DM, MENU_ID = 1, 777


@pytest.fixture(autouse=True)
def fake_runs(monkeypatch):
    runs = registry.discover("tests.seasons.fake_runs")
    monkeypatch.setattr(registry, "all_runs", lambda: runs)
    registry.all_items.cache_clear()
    yield runs
    registry.all_items.cache_clear()


def _seed(session_factory, ids: dict[int, str] | None = None) -> None:
    """ADMIN, BOB, CAROL and Nani Pix are known; CAROL guessed, so she is the
    most active."""
    with session_factory() as session:
        names = ids or {ADMIN: "admin", BOB: "Bob", CAROL: "carol", BOT: "the_bot"}
        for pid, name in names.items():
            players.get_or_create_player(session, pid, username=name)
        session.flush()
        if CAROL in names:
            session.add(Game(id=1, starter_id=ADMIN))
            session.flush()
            session.add(GameGuess(game_id=1, player_id=CAROL, text="x", stage=1))
        session.commit()


def _context(session_factory, args: list[str] | None = None, *, admin: bool = True) -> MagicMock:
    context = MagicMock()
    context.bot.id = BOT
    context.bot_data = {"session_factory": session_factory, "group_chat_id": 555}
    status = ChatMemberStatus.ADMINISTRATOR if admin else ChatMemberStatus.MEMBER
    context.bot.get_chat_member = AsyncMock(return_value=MagicMock(status=status))
    context.args = args or []
    return context


async def _command(session_factory, args: list[str], *, user_id: int = ADMIN) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.type = "private"
    update.message.reply_text = AsyncMock()
    await grant_item_module.grant_item_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _context(session_factory, args))
    )
    return update


async def _tap(
    session_factory, data: str, *, user_id: int = ADMIN, admin: bool = True
) -> MagicMock:
    update = MagicMock()
    query = update.callback_query
    query.data = data
    query.from_user.id = user_id
    query.message.chat.id = DM
    query.message.message_id = MENU_ID
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    await grant_item_picker.grant_item_callback(
        cast(Update, update),
        cast(ContextTypes.DEFAULT_TYPE, _context(session_factory, admin=admin)),
    )
    return query


def _keyboard(markup) -> list[list[tuple[str, str]]]:
    return [[(b.text, b.callback_data) for b in row] for row in markup.inline_keyboard]


def _menu(update: MagicMock) -> tuple[str, list[list[tuple[str, str]]]]:
    update.message.reply_text.assert_awaited_once()
    call = update.message.reply_text.await_args
    return call.args[0], _keyboard(call.kwargs["reply_markup"])


def _owned(session_factory) -> list[tuple[int, str]]:
    with session_factory() as session:
        return [(r.player_id, r.item_id) for r in session.scalars(select(OwnedItem))]


async def test_grantitem_with_only_an_item_lists_every_player_with_its_digest_in_the_data(
    session_factory, log_records
) -> None:
    _seed(session_factory)

    text, rows = _menu(await _command(session_factory, [ITEM_ID]))

    assert text == i18n.t("grant_item.pick_player", "EN", item="Blossom", item_id=ITEM_ID)
    assert rows == [
        [("@carol", f"gi:1:{DIGEST}:t:3")],
        [("@admin", f"gi:1:{DIGEST}:t:1")],
        [("@Bob", f"gi:1:{DIGEST}:t:2")],
        [(CANCEL, f"gi:1:{DIGEST}:x")],
    ]
    assert any(
        r.level == "INFO" and "opened the /grantitem picker" in r.message for r in log_records
    )


async def test_a_pick_grants_like_the_username_form(session_factory) -> None:
    _seed(session_factory)
    typed = await _command(session_factory, ["@bob", ITEM_ID])
    typed_text = typed.message.reply_text.await_args.args[0]
    with session_factory() as session:
        session.query(OwnedItem).delete()
        session.commit()

    query = await _tap(session_factory, f"gi:1:{DIGEST}:t:2")

    assert _owned(session_factory) == [(BOB, ITEM_ID)]
    query.edit_message_text.assert_awaited_once_with(typed_text, reply_markup=None)
    assert typed_text == i18n.t(
        "grant_item.granted", "EN", item="Blossom", item_id=ITEM_ID, player="@Bob"
    )
    with session_factory() as session:
        (row,) = session.scalars(select(OwnedItem))
        assert row.source == ItemSource.ADMIN


@pytest.mark.parametrize("data", ["gi:1:00000000:t:2", "gi:1:00000000:p:0"])
async def test_a_tap_with_an_unknown_digest_is_stale(
    session_factory, data: str, log_records
) -> None:
    _seed(session_factory)

    query = await _tap(session_factory, data)

    query.answer.assert_awaited_once_with(i18n.t("player_picker.stale", "EN"), show_alert=True)
    assert _owned(session_factory) == []
    assert any(r.level == "WARNING" and "stale /grantitem" in r.message for r in log_records)


async def test_an_unknown_item_is_refused_before_the_menu(session_factory) -> None:
    _seed(session_factory)

    update = await _command(session_factory, ["demo_1/name_flair.nope"])

    update.message.reply_text.assert_awaited_once_with(
        i18n.t("grant_item.unknown_item", "EN", item_id="demo_1/name_flair.nope")
    )


async def test_a_pick_rechecks_admin_rights(session_factory, log_records) -> None:
    _seed(session_factory)

    query = await _tap(session_factory, f"gi:1:{DIGEST}:t:2", admin=False)

    query.answer.assert_awaited_once_with(i18n.t("commands.admins_only", "EN"), show_alert=True)
    assert _owned(session_factory) == []
    assert any(r.level == "WARNING" and "no longer an admin" in r.message for r in log_records)


@pytest.mark.parametrize("tail", ["t:2", "p:0", "x"])
async def test_someone_elses_tap_is_refused(session_factory, tail: str) -> None:
    _seed(session_factory)

    query = await _tap(session_factory, f"gi:1:{DIGEST}:{tail}", user_id=BOB)

    query.answer.assert_awaited_once_with(i18n.t("player_picker.not_yours", "EN"), show_alert=True)
    assert _owned(session_factory) == []


@pytest.mark.parametrize("target", [BOT, 99])
async def test_a_pick_of_nani_pix_or_a_stranger_is_stale(session_factory, target: int) -> None:
    _seed(session_factory)

    query = await _tap(session_factory, f"gi:1:{DIGEST}:t:{target}")

    query.answer.assert_awaited_once_with(i18n.t("player_picker.stale", "EN"), show_alert=True)
    assert _owned(session_factory) == []


async def test_a_used_menu_acts_only_once(session_factory, log_records) -> None:
    _seed(session_factory)
    with session_factory() as session:
        session.add(UsedMenu(chat_id=DM, message_id=MENU_ID))
        session.commit()

    query = await _tap(session_factory, f"gi:1:{DIGEST}:t:2")

    query.answer.assert_awaited_once_with(i18n.t("player_picker.stale", "EN"), show_alert=True)
    assert _owned(session_factory) == []
    assert any(r.level == "WARNING" and "used /grantitem menu" in r.message for r in log_records)


async def test_the_menu_pages_with_nine_players(session_factory) -> None:
    _seed(session_factory, {pid: f"p{pid}" for pid in range(10, 19)})

    _, first = _menu(await _command(session_factory, [ITEM_ID], user_id=10))
    query = await _tap(session_factory, f"gi:10:{DIGEST}:p:1", user_id=10)
    text = query.edit_message_text.await_args.args[0]
    second = _keyboard(query.edit_message_text.await_args.kwargs["reply_markup"])

    assert len(first) == 9
    assert first[0] == [("@p10", f"gi:10:{DIGEST}:t:10")]
    assert second[0] == [("@p18", f"gi:10:{DIGEST}:t:18")]
    assert text == i18n.t("grant_item.pick_player", "EN", item="Blossom", item_id=ITEM_ID)


async def test_no_one_to_pick(session_factory) -> None:
    update = await _command(session_factory, [ITEM_ID])

    update.message.reply_text.assert_awaited_once_with(
        i18n.t("grant_item.no_one", "EN"), reply_markup=None
    )


async def test_a_faceless_player_shows_unmasked_in_the_menu_and_masked_after(
    session_factory,
) -> None:
    _seed(session_factory)
    with session_factory() as session:
        add_hex(session, BOB, HexKind.NOPPERABO, hours=2)
        masked = players.display_name(session, BOB)
        session.commit()

    _, rows = _menu(await _command(session_factory, [ITEM_ID]))
    query = await _tap(session_factory, f"gi:1:{DIGEST}:t:2")

    assert [row[0][0] for row in rows[:-1]] == ["@carol", "@admin", "@Bob"]
    assert masked != "@Bob"
    query.edit_message_text.assert_awaited_once_with(
        i18n.t("grant_item.granted", "EN", item="Blossom", item_id=ITEM_ID, player=masked),
        reply_markup=None,
    )


def _every_item_id() -> list[str]:
    """Every item id across the real runs and the test runs."""
    runs = [
        *registry.discover(registry.RUNS_PACKAGE).values(),
        *registry.discover("tests.seasons.fake_runs").values(),
    ]
    return [f"{run.run_id}/{item.key}" for run in runs for item in run.items]


def test_every_registered_item_has_its_own_digest() -> None:
    ids = _every_item_id()
    digests = {grant_item_picker.item_digest(item_id) for item_id in ids}

    assert len(ids) > 20  # the real runs are in there too
    assert len(digests) == len(set(ids))


def test_the_longest_callback_fits_telegrams_64_bytes() -> None:
    longest_id = 2**63 - 1  # 19 digits, the DB's BigInteger bound
    longest_item = max(_every_item_id(), key=len)

    data = grant_item_picker.picker(longest_id, longest_item).target(longest_id)

    assert len(data.encode()) <= 64


def test_a_digest_resolves_back_to_its_item() -> None:
    resolved = grant_item_picker.item_by_digest(DIGEST)

    assert resolved is not None
    assert resolved.item_id == ITEM_ID
    assert grant_item_picker.item_by_digest("00000000") is None
