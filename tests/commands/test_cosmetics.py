from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.commands import cosmetics as cosmetics_module
from nani_pix_bot.models.cosmetics import EquippedItem, OwnedItem
from nani_pix_bot.models.enums import CosmeticSlot, ItemSource, PreviewKind
from nani_pix_bot.models.player import Player
from nani_pix_bot.seasons import registry
from nani_pix_bot.services import i18n
from nani_pix_bot.services.cosmetics import previews
from tests.conftest import LogLine

ME, OTHER = 1, 2
FRAME = "demo_1/host_frame.border"
FLAIR = "demo_1/name_flair.blossom"
IRIS = "demo_1/host_transition.iris"
TITLE = "demo_1/title.demo"


@pytest.fixture(autouse=True)
def fake_runs(monkeypatch):
    runs = registry.discover("tests.seasons.fake_runs")
    monkeypatch.setattr(registry, "all_runs", lambda: runs)
    registry.all_items.cache_clear()
    yield runs
    registry.all_items.cache_clear()


@pytest.fixture(autouse=True)
def players(session_factory) -> None:
    with session_factory() as session:
        session.add_all([Player(telegram_user_id=ME), Player(telegram_user_id=OTHER)])
        session.commit()


def _own(session_factory, player_id: int, item_id: str, *, equip: bool = False) -> int:
    with session_factory() as session:
        row = OwnedItem(player_id=player_id, item_id=item_id, source=ItemSource.ADMIN)
        session.add(row)
        if equip:
            resolved = registry.item(item_id)
            assert resolved is not None
            session.add(EquippedItem(player_id=player_id, slot=resolved.item.slot, item_id=item_id))
        session.commit()
        return row.id


def _equipped(session_factory, player_id: int, slot: CosmeticSlot) -> str | None:
    with session_factory() as session:
        row = session.get(EquippedItem, (player_id, slot))
        return None if row is None else row.item_id


def _context(session_factory, *, member: bool = True) -> MagicMock:
    context = MagicMock()
    context.bot_data = {"session_factory": session_factory, "group_chat_id": 555}
    joined = ChatMemberStatus.MEMBER if member else ChatMemberStatus.LEFT
    context.bot.get_chat_member = AsyncMock(return_value=MagicMock(status=joined))
    sent = MagicMock()
    sent.photo = [MagicMock(file_id="small"), MagicMock(file_id="photo-id")]
    sent.video.file_id = "video-id"
    context.bot.send_photo = AsyncMock(return_value=sent)
    context.bot.send_video = AsyncMock(return_value=sent)
    context.bot.send_message = AsyncMock()
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


async def _command(session_factory, *, member: bool = True) -> MagicMock:
    update = _command_update()
    await cosmetics_module.cosmetics_command(
        cast(Update, update),
        cast(ContextTypes.DEFAULT_TYPE, _context(session_factory, member=member)),
    )
    return update


async def _tap(session_factory, data: str, context: MagicMock | None = None) -> MagicMock:
    update = _callback_update(data)
    await cosmetics_module.cosmetics_callback(
        cast(Update, update),
        cast(ContextTypes.DEFAULT_TYPE, context or _context(session_factory)),
    )
    update.callback_query.answer.assert_awaited_once()
    return update


def _buttons(markup: InlineKeyboardMarkup) -> list[tuple[str, str]]:
    return [(b.text, str(b.callback_data)) for row in markup.inline_keyboard for b in row]


def _edit(update: MagicMock) -> tuple[str, list[tuple[str, str]]]:
    call = update.callback_query.edit_message_text.await_args
    return call.args[0], _buttons(call.kwargs["reply_markup"])


# --- /cosmetics ---


async def test_menu_with_nothing_owned_says_so(session_factory) -> None:
    update = await _command(session_factory)
    call = update.message.reply_text.await_args
    assert call.args[0] == i18n.t("cosmetics.empty", "EN")
    assert "reply_markup" not in call.kwargs


async def test_menu_lists_slots_with_counts_and_what_is_equipped(session_factory) -> None:
    _own(session_factory, ME, FRAME, equip=True)
    _own(session_factory, ME, FLAIR)
    update = await _command(session_factory)

    call = update.message.reply_text.await_args
    text = call.args[0]
    assert "Border" in text
    assert text.count("1") >= 2  # one owned in each of the two slots
    assert _buttons(call.kwargs["reply_markup"]) == [
        (i18n.t("cosmetics.slot.host_frame", "EN"), "cos:s:host_frame"),
        (i18n.t("cosmetics.slot.name_flair", "EN"), "cos:s:name_flair"),
    ]


async def test_menu_refuses_a_non_member(session_factory) -> None:
    _own(session_factory, ME, FRAME)
    update = await _command(session_factory, member=False)
    assert update.message.reply_text.await_args.args[0] == i18n.t("dm_start.not_a_member", "EN")


async def test_menu_in_a_group_is_ignored(session_factory) -> None:
    update = _command_update()
    update.effective_chat.type = "supergroup"
    await cosmetics_module.cosmetics_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _context(session_factory))
    )
    update.message.reply_text.assert_not_awaited()


# --- slot view / back ---


async def test_slot_view_lists_owned_items_marking_the_equipped_one(session_factory) -> None:
    owned_id = _own(session_factory, ME, FRAME, equip=True)
    update = await _tap(session_factory, "cos:s:host_frame")

    text, buttons = _edit(update)
    assert i18n.t("cosmetics.slot.host_frame", "EN") in text
    assert buttons == [
        ("✅ Border", f"cos:i:{owned_id}"),
        (i18n.t("cosmetics.back", "EN"), "cos:m"),
    ]


async def test_back_returns_to_the_menu(session_factory) -> None:
    _own(session_factory, ME, FRAME)
    update = await _tap(session_factory, "cos:m")
    _text, buttons = _edit(update)
    assert buttons == [(i18n.t("cosmetics.slot.host_frame", "EN"), "cos:s:host_frame")]


async def test_a_malformed_tap_is_answered_and_ignored(session_factory) -> None:
    update = await _tap(session_factory, "cos:s:nonsense")
    update.callback_query.edit_message_text.assert_not_awaited()


# --- item tap: previews ---


async def test_item_tap_renders_sends_and_remembers_the_preview(session_factory) -> None:
    owned_id = _own(session_factory, ME, FRAME)
    context = _context(session_factory)
    await _tap(session_factory, f"cos:i:{owned_id}", context)

    photo = context.bot.send_photo.await_args.kwargs["photo"]
    assert isinstance(photo, bytes)
    with session_factory() as session:
        row = previews.cached(session, FRAME, "EN")
        assert row is not None
        assert (row.kind, row.file_id) == (PreviewKind.PHOTO, "photo-id")
    item_message = context.bot.send_message.await_args
    assert _buttons(item_message.kwargs["reply_markup"]) == [
        (i18n.t("cosmetics.equip", "EN"), f"cos:e:{owned_id}"),
        (i18n.t("cosmetics.back", "EN"), "cos:s:host_frame"),
    ]


async def test_item_tap_still_sends_the_equip_message_when_the_preview_fails(
    session_factory, log_records: list[LogLine]
) -> None:
    owned_id = _own(session_factory, ME, FRAME)
    context = _context(session_factory)
    context.bot.send_photo = AsyncMock(side_effect=TelegramError("telegram is down"))
    await _tap(session_factory, f"cos:i:{owned_id}", context)

    assert any(r.level == "ERROR" for r in log_records)
    item_message = context.bot.send_message.await_args
    assert (i18n.t("cosmetics.equip", "EN"), f"cos:e:{owned_id}") in _buttons(
        item_message.kwargs["reply_markup"]
    )


async def test_item_tap_on_someone_elses_item_is_refused(
    session_factory, log_records: list[LogLine]
) -> None:
    theirs = _own(session_factory, OTHER, FRAME)
    context = _context(session_factory)
    update = await _tap(session_factory, f"cos:i:{theirs}", context)

    assert update.callback_query.answer.await_args.args[0] == i18n.t("cosmetics.not_yours", "EN")
    context.bot.send_photo.assert_not_awaited()
    assert any(r.level == "WARNING" for r in log_records)


# --- equip / unequip ---


async def test_equip_equips_the_item(session_factory) -> None:
    owned_id = _own(session_factory, ME, FRAME)
    update = await _tap(session_factory, f"cos:e:{owned_id}")

    assert _equipped(session_factory, ME, CosmeticSlot.HOST_FRAME) == FRAME
    text, buttons = _edit(update)
    assert text.startswith(i18n.t("cosmetics.equipped", "EN", item="Border"))
    assert buttons[0] == ("✅ Border", f"cos:i:{owned_id}")


async def test_unequip_clears_the_slot(session_factory) -> None:
    owned_id = _own(session_factory, ME, FRAME, equip=True)
    update = await _tap(session_factory, "cos:u:host_frame")

    assert _equipped(session_factory, ME, CosmeticSlot.HOST_FRAME) is None
    text, buttons = _edit(update)
    assert text.startswith(i18n.t("cosmetics.unequipped", "EN"))
    assert buttons[0] == ("Border", f"cos:i:{owned_id}")


async def test_a_forged_equip_of_another_players_item_is_refused(
    session_factory, log_records: list[LogLine]
) -> None:
    theirs = _own(session_factory, OTHER, FRAME)
    update = await _tap(session_factory, f"cos:e:{theirs}")

    assert _equipped(session_factory, ME, CosmeticSlot.HOST_FRAME) is None
    assert update.callback_query.answer.await_args.args[0] == i18n.t("cosmetics.not_yours", "EN")
    update.callback_query.edit_message_text.assert_not_awaited()
    assert any(r.level == "WARNING" for r in log_records)


async def test_equip_of_an_unknown_owned_id_is_refused(session_factory) -> None:
    update = await _tap(session_factory, "cos:e:999")
    assert update.callback_query.answer.await_args.args[0] == i18n.t("cosmetics.not_yours", "EN")


# --- titles: owned here, picked in /title ---


async def test_a_title_item_has_no_equip_button_and_points_to_title(session_factory) -> None:
    owned_id = _own(session_factory, ME, TITLE)
    context = _context(session_factory)
    await _tap(session_factory, f"cos:i:{owned_id}", context)

    preview, item_message = context.bot.send_message.await_args_list
    assert preview.kwargs["text"] == "«Demo Star»"
    assert i18n.t("cosmetics.title_hint", "EN") in item_message.kwargs["text"]
    assert _buttons(item_message.kwargs["reply_markup"]) == [
        (i18n.t("cosmetics.back", "EN"), "cos:s:title"),
    ]


async def test_a_forged_equip_of_a_title_item_equips_nothing(session_factory) -> None:
    owned_id = _own(session_factory, ME, TITLE)
    update = await _tap(session_factory, f"cos:e:{owned_id}")

    assert _equipped(session_factory, ME, CosmeticSlot.TITLE) is None
    assert _edit(update)[0].startswith(i18n.t("cosmetics.title_hint", "EN"))
