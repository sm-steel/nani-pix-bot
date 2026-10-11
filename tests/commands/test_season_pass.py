from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Update
from telegram.constants import ChatMemberStatus, ChatType
from telegram.ext import ContextTypes

from nani_pix_bot.commands import season_pass as season_pass_module
from nani_pix_bot.models.enums import SeasonStatus
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.services import i18n
from nani_pix_bot.services.seasons import schedule

NOW = datetime(2026, 11, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def fake_runs(monkeypatch):
    runs = registry.discover("tests.seasons.fake_runs")
    monkeypatch.setattr(registry, "all_runs", lambda: runs)
    return runs


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


def _update(*, chat_type: str = ChatType.PRIVATE) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = 1
    update.effective_user.username = "me"
    update.effective_chat.id = 555 if chat_type != ChatType.PRIVATE else 1
    update.effective_chat.type = chat_type
    update.message.message_thread_id = 7 if chat_type != ChatType.PRIVATE else None
    update.message.reply_text = AsyncMock()
    return update


def _context(session_factory, *, member: bool = True) -> MagicMock:
    context = MagicMock()
    joined = ChatMemberStatus.MEMBER if member else ChatMemberStatus.LEFT
    context.bot.get_chat_member = AsyncMock(return_value=MagicMock(status=joined))
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    return context


async def _run(session_factory, update: MagicMock, *, member: bool = True) -> str:
    await season_pass_module.season_pass_command(
        cast(Update, update),
        cast(ContextTypes.DEFAULT_TYPE, _context(session_factory, member=member)),
    )
    return update.message.reply_text.await_args.args[0]


async def test_the_command_replies_in_dm(session_factory, season) -> None:
    text = await _run(session_factory, _update())
    assert "Demo Season" in text
    assert i18n.t("season_pass.view.objectives", "EN") in text


async def test_outside_a_dm_it_says_to_use_the_dm(session_factory, season) -> None:
    text = await _run(session_factory, _update(chat_type=ChatType.SUPERGROUP))
    assert text == i18n.t("season_pass.dm_only", "EN")


async def test_a_non_member_is_refused(session_factory, season) -> None:
    text = await _run(session_factory, _update(), member=False)
    assert text == i18n.t("dm_start.not_a_member", "EN")


async def test_with_no_season_it_says_so(session_factory) -> None:
    assert await _run(session_factory, _update()) == i18n.t("season_pass.none", "EN")


async def test_a_scheduled_season_shows_an_empty_pass(session_factory) -> None:
    _add_season(session_factory, SeasonStatus.SCHEDULED, offset_days=1)
    assert "0 XP" in await _run(session_factory, _update())


async def test_showing_the_pass_is_logged(session_factory, season, log_records) -> None:
    await _run(session_factory, _update())
    assert any(r.level == "INFO" and "/seasonpass" in r.message for r in log_records)


# --- Premium buttons ---


def _buttons(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def _query(data: str, *, user_id: int = 1) -> MagicMock:
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.from_user.id = user_id
    update.callback_query.from_user.username = "me"
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


async def _tap(session_factory, data: str, *, member: bool = True, user_id: int = 1) -> MagicMock:
    update = _query(data, user_id=user_id)
    await season_pass_module.season_pass_callback(
        cast(Update, update),
        cast(ContextTypes.DEFAULT_TYPE, _context(session_factory, member=member)),
    )
    return update.callback_query


def _set_currency(session_factory, amount: int) -> None:
    with session_factory() as session:
        session.get(Player, 1).currency = amount
        session.commit()


def _season_id(session_factory) -> int:
    with session_factory() as session:
        current = schedule.current(session)
        assert current is not None
        return current.id


def _alert(query: MagicMock) -> str:
    assert query.answer.await_args.kwargs.get("show_alert") is True
    return query.answer.await_args.args[0]


async def test_the_view_offers_premium_while_it_is_unbought(session_factory, season) -> None:
    update = _update()
    await _run(session_factory, update)
    markup = update.message.reply_text.await_args.kwargs["reply_markup"]
    sid = _season_id(session_factory)
    assert _buttons(markup) == [f"spass:buy:{sid}"]
    assert markup.inline_keyboard[0][0].text == i18n.t("season_pass.buy", "EN", price=150)


async def test_no_buy_button_once_premium_is_bought(session_factory, season) -> None:
    _set_currency(session_factory, 1000)
    sid = _season_id(session_factory)
    await _tap(session_factory, f"spass:yes:{sid}")
    update = _update()
    await _run(session_factory, update)
    assert update.message.reply_text.await_args.kwargs["reply_markup"] is None


async def test_no_buy_button_while_the_season_is_closing(session_factory) -> None:
    _add_season(session_factory, SeasonStatus.CLOSING)
    update = _update()
    await _run(session_factory, update)
    assert update.message.reply_text.await_args.kwargs["reply_markup"] is None


async def test_buy_asks_to_confirm(session_factory, season) -> None:
    sid = _season_id(session_factory)
    query = await _tap(session_factory, f"spass:buy:{sid}")
    text = query.edit_message_text.await_args.args[0]
    assert text == i18n.t("season_pass.confirm", "EN", price=150)
    markup = query.edit_message_text.await_args.kwargs["reply_markup"]
    assert _buttons(markup) == [f"spass:yes:{sid}", f"spass:no:{sid}"]
    query.answer.assert_awaited_once_with()


async def test_yes_buys_and_shows_the_view(session_factory, season) -> None:
    _set_currency(session_factory, 1000)
    sid = _season_id(session_factory)
    query = await _tap(session_factory, f"spass:yes:{sid}")
    query.answer.assert_awaited_once_with()
    assert query.edit_message_text.await_args.kwargs.get("reply_markup") is None
    assert "⭐ Premium" in query.edit_message_text.await_args.args[0]
    with session_factory() as session:
        assert session.get(Player, 1).currency == 850


async def test_no_goes_back_to_the_view(session_factory, season) -> None:
    sid = _season_id(session_factory)
    query = await _tap(session_factory, f"spass:no:{sid}")
    assert "Demo Season" in query.edit_message_text.await_args.args[0]
    assert _buttons(query.edit_message_text.await_args.kwargs["reply_markup"]) == [
        f"spass:buy:{sid}"
    ]


async def test_too_poor_gets_an_alert_with_the_balance(session_factory, season) -> None:
    _set_currency(session_factory, 10)
    sid = _season_id(session_factory)
    query = await _tap(session_factory, f"spass:yes:{sid}")
    assert _alert(query) == i18n.t("season_pass.refused.insufficient", "EN", price=150, balance=10)
    query.edit_message_text.assert_not_awaited()


async def test_buying_twice_is_refused(session_factory, season) -> None:
    _set_currency(session_factory, 1000)
    sid = _season_id(session_factory)
    await _tap(session_factory, f"spass:yes:{sid}")
    query = await _tap(session_factory, f"spass:yes:{sid}")
    assert _alert(query) == i18n.t("season_pass.refused.already", "EN")
    with session_factory() as session:
        assert session.get(Player, 1).currency == 850


async def test_an_old_messages_season_is_not_open(session_factory, season) -> None:
    _set_currency(session_factory, 1000)
    sid = _season_id(session_factory)
    query = await _tap(session_factory, f"spass:yes:{sid + 5}")
    assert _alert(query) == i18n.t("season_pass.refused.not_open", "EN")
    with session_factory() as session:
        assert session.get(Player, 1).currency == 1000


async def test_a_confirm_for_an_old_season_is_not_open(session_factory, season) -> None:
    sid = _season_id(session_factory)
    query = await _tap(session_factory, f"spass:buy:{sid + 5}")
    assert _alert(query) == i18n.t("season_pass.refused.not_open", "EN")


async def test_no_season_at_all_is_not_open(session_factory) -> None:
    query = await _tap(session_factory, "spass:buy:1")
    assert _alert(query) == i18n.t("season_pass.refused.not_open", "EN")


@pytest.mark.parametrize(
    "data",
    [
        "spass:",
        "spass:buy",
        "spass:buy:",
        "spass:buy:x",
        "spass:zap:1",
        "spass:buy:-1",
        f"spass:buy:{2**63}",
    ],
)
async def test_a_malformed_tap_is_answered_and_ignored(
    session_factory, season, log_records, data
) -> None:
    query = await _tap(session_factory, data)
    query.answer.assert_awaited_once_with()
    query.edit_message_text.assert_not_awaited()
    assert any(r.level == "WARNING" and "malformed" in r.message for r in log_records)


async def test_a_non_member_cannot_buy(session_factory, season) -> None:
    _set_currency(session_factory, 1000)
    sid = _season_id(session_factory)
    query = await _tap(session_factory, f"spass:yes:{sid}", member=False)
    assert _alert(query) == i18n.t("dm_start.not_a_member", "EN")
    with session_factory() as session:
        assert session.get(Player, 1).currency == 1000


async def test_a_failing_purchase_answers_the_tap_and_charges_nothing(
    session_factory, season, monkeypatch, log_records
) -> None:
    _set_currency(session_factory, 1000)
    sid = _season_id(session_factory)

    def boom(*_args, **_kwargs):
        raise RuntimeError("db went away")

    monkeypatch.setattr(season_pass_module.season_pass, "buy_premium", boom)
    query = await _tap(session_factory, f"spass:yes:{sid}")
    assert _alert(query) == i18n.t("season_pass.failed", "EN")
    assert any(record.level == "ERROR" for record in log_records)
    with session_factory() as session:
        assert session.get(Player, 1).currency == 1000


async def test_an_unopen_season_buy_is_logged(session_factory, season, log_records) -> None:
    sid = _season_id(session_factory)
    await _tap(session_factory, f"spass:yes:{sid + 5}")
    assert any(
        record.level == "WARNING" and record.extra.get("tapped_season_id") == sid + 5
        for record in log_records
    )
