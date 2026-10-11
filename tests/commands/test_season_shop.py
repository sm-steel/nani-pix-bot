from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Update
from telegram.constants import ChatMemberStatus, ChatType
from telegram.ext import ContextTypes

from nani_pix_bot.commands import season_shop as season_shop_module
from nani_pix_bot.models.enums import CurrencyReason, SeasonStatus
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.services import i18n
from nani_pix_bot.services.economy import wallet
from nani_pix_bot.services.economy.wallet import LedgerEntry
from tests.conftest import LogLine

NOW = datetime(2026, 11, 1, tzinfo=UTC)


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
        session.add(Player(telegram_user_id=1, username="me"))
        session.commit()


def _add_season(session_factory, status: SeasonStatus, *, offset_days: int = -1) -> None:
    with session_factory() as session:
        session.add(
            SeasonSchedule(
                run_id="demo_1",
                start_at=NOW + timedelta(days=offset_days),
                end_at=NOW + timedelta(days=9),
                status=status,
                created_by=9,
            )
        )
        session.commit()


@pytest.fixture
def season(session_factory) -> None:
    _add_season(session_factory, SeasonStatus.ACTIVE)


@pytest.fixture
def coins(session_factory, season) -> None:
    with session_factory() as session:
        player = session.get(Player, 1)
        assert player is not None
        entry = LedgerEntry(CurrencyReason.SEASON_WIN, season_id=1, coins=True)
        wallet.credit(session, player, 200, entry)
        session.commit()


def _update(*, chat_type: str = ChatType.PRIVATE) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = 1
    update.effective_user.username = "me"
    update.effective_chat.id = 555 if chat_type != ChatType.PRIVATE else 1
    update.effective_chat.type = chat_type
    update.message.reply_text = AsyncMock()
    return update


def _context(session_factory, *, member: bool = True) -> MagicMock:
    context = MagicMock()
    joined = ChatMemberStatus.MEMBER if member else ChatMemberStatus.LEFT
    context.bot.get_chat_member = AsyncMock(return_value=MagicMock(status=joined))
    context.bot.send_message = AsyncMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    return context


async def _run(session_factory, update: MagicMock, *, member: bool = True) -> str:
    await season_shop_module.season_shop_command(
        cast(Update, update),
        cast(ContextTypes.DEFAULT_TYPE, _context(session_factory, member=member)),
    )
    return update.message.reply_text.await_args.args[0]


async def _tap(session_factory, context: MagicMock, data: str) -> MagicMock:
    query = MagicMock()
    query.data = data
    query.from_user.id = 1
    query.from_user.username = "me"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    await season_shop_module.season_shop_callback(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )
    return query


def _callbacks(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


async def test_the_menu_lists_offers_and_balance(session_factory, season, coins) -> None:
    text = await _run(session_factory, _update())
    for expected in ("Demo Season", "200", "Dango", "Border"):
        assert expected in text


async def test_the_menu_has_a_button_per_offer(session_factory, season, coins) -> None:
    update = _update()
    await _run(session_factory, update)
    markup = update.message.reply_text.await_args.kwargs["reply_markup"]
    assert _callbacks(markup) == ["sshop:i:1:0", "sshop:i:1:1", "sshop:i:1:2"]


async def test_the_menu_shows_the_clue_token_count_left(session_factory, season, coins) -> None:
    text = await _run(session_factory, _update())
    assert i18n.t("season_shop.left", "EN", left=2) in text


async def test_an_owned_offer_is_marked(session_factory, season, coins) -> None:
    await _tap(session_factory, _context(session_factory), "sshop:b:1:0")
    text = await _run(session_factory, _update())
    assert "✅" in text


async def test_the_shop_is_closed_while_the_season_is_scheduled(session_factory) -> None:
    _add_season(session_factory, SeasonStatus.SCHEDULED, offset_days=1)
    assert await _run(session_factory, _update()) == i18n.t("season_shop.closed", "EN")


async def test_the_shop_is_closed_with_no_season(session_factory) -> None:
    assert await _run(session_factory, _update()) == i18n.t("season_shop.closed", "EN")


async def test_outside_a_dm_it_says_to_use_the_dm(session_factory, season) -> None:
    text = await _run(session_factory, _update(chat_type=ChatType.SUPERGROUP))
    assert text == i18n.t("season_shop.dm_only", "EN")


async def test_a_non_member_is_refused(session_factory, season) -> None:
    text = await _run(session_factory, _update(), member=False)
    assert text == i18n.t("dm_start.not_a_member", "EN")


async def test_opening_the_shop_is_logged(
    session_factory, season, coins, log_records: list[LogLine]
) -> None:
    await _run(session_factory, _update())
    assert any(r.level == "INFO" and "/seasonshop" in r.message for r in log_records)


async def test_opening_an_item_sends_its_preview_first(
    session_factory, season, coins, monkeypatch
) -> None:
    sent = AsyncMock()
    monkeypatch.setattr(season_shop_module.item_preview, "send", sent)
    context = _context(session_factory)
    await _tap(session_factory, context, "sshop:i:1:0")
    assert sent.await_args_list[0].args[2].item_id == "demo_1/name_flair.dango"
    markup = context.bot.send_message.await_args.kwargs["reply_markup"]
    assert _callbacks(markup) == ["sshop:b:1:0", "sshop:m:1"]


async def test_an_owned_item_offers_only_back(session_factory, season, coins, monkeypatch) -> None:
    monkeypatch.setattr(season_shop_module.item_preview, "send", AsyncMock())
    context = _context(session_factory)
    await _tap(session_factory, context, "sshop:b:1:0")
    await _tap(session_factory, context, "sshop:i:1:0")
    markup = context.bot.send_message.await_args.kwargs["reply_markup"]
    assert _callbacks(markup) == ["sshop:m:1"]


async def test_opening_the_clue_token_sends_its_icon(
    session_factory, season, coins, monkeypatch
) -> None:
    sent = AsyncMock()
    monkeypatch.setattr(season_shop_module.item_preview, "send_image", sent)
    context = _context(session_factory)
    await _tap(session_factory, context, "sshop:i:1:2")
    assert sent.await_args_list[0].args[2].cache_key == "consumable/clue_token"
    assert (
        i18n.t("season_shop.offer_token_note", "EN")
        in context.bot.send_message.await_args.kwargs["text"]
    )


async def test_opening_an_offer_that_does_not_exist_is_refused(
    session_factory, season, coins
) -> None:
    context = _context(session_factory)
    query = await _tap(session_factory, context, "sshop:i:1:9")
    assert query.answer.await_args.args[0] == i18n.t("season_shop.refused.unknown", "EN")
    context.bot.send_message.assert_not_awaited()


async def test_opening_an_item_is_logged(
    session_factory, season, coins, monkeypatch, log_records: list[LogLine]
) -> None:
    monkeypatch.setattr(season_shop_module.item_preview, "send", AsyncMock())
    await _tap(session_factory, _context(session_factory), "sshop:i:1:0")
    assert any(r.level == "INFO" and "offer" in r.message for r in log_records)


async def test_back_edits_the_message_to_the_menu(session_factory, season, coins) -> None:
    query = await _tap(session_factory, _context(session_factory), "sshop:m:1")
    assert "Demo Season" in query.edit_message_text.await_args.args[0]


async def test_buying_edits_the_message_to_the_result(session_factory, season, coins) -> None:
    query = await _tap(session_factory, _context(session_factory), "sshop:b:1:0")
    text = query.edit_message_text.await_args.args[0]
    assert "Dango" in text
    assert "160" in text
    assert i18n.t("season_shop.equip_hint", "EN") in text
    assert _callbacks(query.edit_message_text.await_args.kwargs["reply_markup"]) == ["sshop:m:1"]
    with session_factory() as session:
        assert wallet.balance(session, 1, season_id=1) == 160


async def test_buying_the_clue_token_has_no_equip_hint(session_factory, season, coins) -> None:
    query = await _tap(session_factory, _context(session_factory), "sshop:b:1:2")
    text = query.edit_message_text.await_args.args[0]
    assert i18n.t("season_pass.reward.clue_token", "EN") in text
    assert i18n.t("season_shop.equip_hint", "EN") not in text


async def test_a_refused_buy_is_an_alert(session_factory, season, coins) -> None:
    context = _context(session_factory)
    await _tap(session_factory, context, "sshop:b:1:0")
    query = await _tap(session_factory, context, "sshop:b:1:0")
    assert query.answer.await_args.kwargs["show_alert"] is True
    assert query.answer.await_args.args[0] == i18n.t("season_shop.refused.owned", "EN")


async def test_an_unaffordable_buy_names_price_and_balance(session_factory, season) -> None:
    query = await _tap(session_factory, _context(session_factory), "sshop:b:1:1")
    expected = i18n.t("season_shop.refused.insufficient", "EN", price=80, balance=0)
    assert query.answer.await_args.args[0] == expected


async def test_a_buy_tap_for_an_old_season_is_refused(
    session_factory, season, coins, log_records: list[LogLine]
) -> None:
    query = await _tap(session_factory, _context(session_factory), "sshop:b:99:0")
    assert query.answer.await_args.args[0] == i18n.t("season_shop.closed", "EN")
    with session_factory() as session:
        assert wallet.balance(session, 1, season_id=1) == 200
    assert any(r.level == "WARNING" for r in log_records)


async def test_a_buy_that_blows_up_is_an_alert_and_logged(
    session_factory, season, coins, monkeypatch, log_records: list[LogLine]
) -> None:
    def boom(*_args):
        raise RuntimeError("db down")

    monkeypatch.setattr(season_shop_module.season_shop, "buy", boom)
    query = await _tap(session_factory, _context(session_factory), "sshop:b:1:0")
    assert query.answer.await_args.args[0] == i18n.t("season_shop.failed", "EN")
    assert any(r.level == "ERROR" for r in log_records)


async def test_a_non_member_tap_is_refused(session_factory, season, coins) -> None:
    query = await _tap(session_factory, _context(session_factory, member=False), "sshop:b:1:0")
    assert query.answer.await_args.args[0] == i18n.t("dm_start.not_a_member", "EN")
    with session_factory() as session:
        assert wallet.balance(session, 1, season_id=1) == 200


async def test_malformed_callback_data_is_ignored(
    session_factory, season, coins, log_records: list[LogLine]
) -> None:
    query = await _tap(session_factory, _context(session_factory), "sshop:b:x:y")
    query.edit_message_text.assert_not_awaited()
    assert any(r.level == "WARNING" for r in log_records)
