from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes

from nani_pix_bot.commands import grant_item as grant_item_module
from nani_pix_bot.models.cosmetics import OwnedItem
from nani_pix_bot.models.enums import ItemSource
from nani_pix_bot.seasons import registry
from nani_pix_bot.services import i18n, players

ADMIN = 1
TARGET = 77
ITEM_ID = "demo_1/name_flair.blossom"


@pytest.fixture(autouse=True)
def fake_runs(monkeypatch):
    runs = registry.discover("tests.seasons.fake_runs")
    monkeypatch.setattr(registry, "all_runs", lambda: runs)
    registry.all_items.cache_clear()
    yield runs
    registry.all_items.cache_clear()


@pytest.fixture(autouse=True)
def target_player(session_factory) -> None:
    with session_factory() as session:
        players.get_or_create_player(session, TARGET, username="Bob")
        session.commit()


def _context(session_factory, args: list[str], *, admin: bool = True) -> MagicMock:
    context = MagicMock()
    context.bot_data = {"session_factory": session_factory, "group_chat_id": 555}
    status = ChatMemberStatus.ADMINISTRATOR if admin else ChatMemberStatus.MEMBER
    context.bot.get_chat_member = AsyncMock(return_value=MagicMock(status=status))
    context.args = args
    return context


def _update() -> MagicMock:
    update = MagicMock()
    update.effective_user.id = ADMIN
    update.effective_chat.type = "private"
    update.message.reply_text = AsyncMock()
    return update


def _reply_text(update: MagicMock) -> str:
    args, kwargs = update.message.reply_text.await_args
    return args[0] if args else kwargs["text"]


async def _run(session_factory, args: list[str], *, admin: bool = True) -> MagicMock:
    update = _update()
    await grant_item_module.grant_item_command(
        cast(Update, update),
        cast(ContextTypes.DEFAULT_TYPE, _context(session_factory, args, admin=admin)),
    )
    return update


def _owned(session_factory) -> list[OwnedItem]:
    with session_factory() as session:
        return list(session.scalars(select(OwnedItem)))


async def test_grants_a_known_item_to_a_known_player(session_factory) -> None:
    update = await _run(session_factory, ["@bob", ITEM_ID])
    (row,) = _owned(session_factory)
    assert (row.player_id, row.item_id, row.source, row.season_id) == (
        TARGET,
        ITEM_ID,
        ItemSource.ADMIN,
        None,
    )
    assert _reply_text(update) == i18n.t(
        "grant_item.granted", "EN", item="Blossom", item_id=ITEM_ID, player="@Bob"
    )


async def test_a_second_grant_is_already_owned(session_factory) -> None:
    await _run(session_factory, ["bob", ITEM_ID])
    update = await _run(session_factory, ["@bob", ITEM_ID])
    assert len(_owned(session_factory)) == 1
    assert _reply_text(update) == i18n.t(
        "grant_item.already_owned", "EN", item="Blossom", item_id=ITEM_ID, player="@Bob"
    )


async def test_unknown_item_is_refused(session_factory) -> None:
    update = await _run(session_factory, ["@bob", "demo_1/name_flair.nope"])
    assert _owned(session_factory) == []
    assert _reply_text(update) == i18n.t(
        "grant_item.unknown_item", "EN", item_id="demo_1/name_flair.nope"
    )


async def test_unknown_player_is_refused(session_factory) -> None:
    update = await _run(session_factory, ["@ghost", ITEM_ID])
    assert _owned(session_factory) == []
    assert _reply_text(update) == i18n.t("grant_item.unknown_player", "EN", username="ghost")


async def test_non_admin_is_refused(session_factory) -> None:
    update = await _run(session_factory, ["@bob", ITEM_ID], admin=False)
    assert _owned(session_factory) == []
    assert _reply_text(update) == i18n.t("commands.admins_only", "EN")


@pytest.mark.parametrize("args", [[], ["@bob"], ["@bob", ITEM_ID, "extra"]])
async def test_bad_argument_count_shows_usage(session_factory, args) -> None:
    update = await _run(session_factory, args)
    assert _owned(session_factory) == []
    assert _reply_text(update) == i18n.t("grant_item.usage", "EN")
