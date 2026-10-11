from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from nani_pix_bot.commands.achievements.common import MAX_ID
from nani_pix_bot.commands.profile import browse, edit
from nani_pix_bot.commands.profile import card as card_module
from nani_pix_bot.commands.profile import data as cb
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.achievement import AchievementGrant
from nani_pix_bot.models.enums import Rarity, SeasonStatus, ShowcaseKind
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonResult, SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.services import i18n
from nani_pix_bot.services.cosmetics import showcase

ME = 1
NOW = datetime(2026, 1, 1, tzinfo=UTC)
STAMP, ACH = ShowcaseKind.STAMP, ShowcaseKind.ACHIEVEMENT


@pytest.fixture(autouse=True)
def fake_runs(monkeypatch):
    runs = registry.discover("tests.seasons.fake_runs")
    monkeypatch.setattr(registry, "all_runs", lambda: runs)
    registry.all_items.cache_clear()
    yield runs
    registry.all_items.cache_clear()


@pytest.fixture(autouse=True)
def me(session_factory) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=ME, username="me"))
        session.commit()


def _season(session_factory, run_id: str, days: int) -> int:
    with session_scope(session_factory) as session:
        row = SeasonSchedule(
            run_id=run_id,
            start_at=NOW + timedelta(days=days),
            end_at=NOW + timedelta(days=days + 5),
            status=SeasonStatus.ENDED,
            created_by=9,
        )
        session.add(row)
        session.flush()
        session.add(SeasonResult(season_id=row.id, player_id=ME, rank=1, xp=10))
        return row.id


def _grant(session_factory, key: str, rarity: Rarity = Rarity.GOLD) -> int:
    with session_scope(session_factory) as session:
        row = AchievementGrant(
            player_id=ME, key=key, tier=1, period_key="", rarity=rarity, reward=0, points=0
        )
        session.add(row)
        session.flush()
        return row.id


def _query(data: str, *, chat_type: str = ChatType.PRIVATE) -> MagicMock:
    query = MagicMock()
    query.data = data
    query.from_user.id = ME
    query.message.chat.type = chat_type
    query.answer = AsyncMock()
    query.edit_message_caption = AsyncMock()
    query.edit_message_media = AsyncMock()
    return query


async def _tap(session_factory, query: MagicMock) -> None:
    update = MagicMock()
    update.callback_query = query
    context = MagicMock()
    context.bot_data = {"session_factory": session_factory}
    await browse.profile_callback(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))


def _buttons(query: MagicMock) -> list[list[tuple[str, str]]]:
    markup = query.edit_message_caption.await_args.kwargs["reply_markup"]
    return [[(b.text, b.callback_data) for b in row] for row in markup.inline_keyboard]


# --- callback data -----------------------------------------------------------------


def test_editor_callback_data_round_trips() -> None:
    assert cb.parse(cb.edit_data(ME, ACH, 2)) == ("e", [ME, "a", 2])
    assert cb.parse(cb.toggle_data(ME, STAMP, 77, 0)) == ("t", [ME, "s", 77, 0])


@pytest.mark.parametrize("data", ["prof:e:1:x:0", "prof:t:1:x:5:0", "prof:t:1:s:5"])
def test_malformed_editor_data_is_rejected(data: str) -> None:
    assert cb.parse(data) is None


def test_the_longest_toggle_data_fits_telegrams_64_bytes() -> None:
    assert len(cb.toggle_data(MAX_ID, ACH, MAX_ID, 99).encode()) <= 64


# --- the ✏️ button -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("owner", "target", "private", "expected"),
    [(ME, ME, True, True), (ME, ME, False, False), (ME, 2, True, False)],
)
def test_only_your_own_card_in_dm_is_editable(owner, target, private, expected) -> None:
    assert browse.Viewing(owner, target, private=private).editable is expected


def test_an_editable_card_has_the_edit_button_last() -> None:
    markup = browse.card_keyboard([ME], browse.Viewing(ME, ME, private=True), "EN")
    assert markup.inline_keyboard[-1][0].callback_data == cb.edit_data(ME, STAMP, 0)


# --- the editor ----------------------------------------------------------------------


async def test_the_editor_lists_seasons_with_marks_and_a_done_button(session_factory) -> None:
    older = _season(session_factory, "demo_1", 0)
    newer = _season(session_factory, "other_1", 30)
    with session_scope(session_factory) as session:
        showcase.toggle(session, ME, STAMP, "demo_1")
    query = _query(cb.edit_data(ME, STAMP, 0))
    await _tap(session_factory, query)

    rows = _buttons(query)
    assert [data for _text, data in rows[0]] == [
        cb.edit_data(ME, STAMP, 0),
        cb.edit_data(ME, ACH, 0),
    ]
    assert rows[1] == [(edit.HIDDEN + "other_1", cb.toggle_data(ME, STAMP, newer, 0))]
    assert rows[2] == [(edit.SHOWN + "Demo Season", cb.toggle_data(ME, STAMP, older, 0))]
    assert rows[-1] == [(i18n.t("profile.edit.done", "EN"), cb.card_data(ME, ME, "m"))]
    caption = query.edit_message_caption.await_args.kwargs["caption"]
    assert caption == i18n.t("profile.edit.stamps", "EN", count=1, limit=showcase.STAMPS_MAX)


async def test_with_nothing_held_the_editor_says_so(session_factory) -> None:
    query = _query(cb.edit_data(ME, ACH, 0))
    await _tap(session_factory, query)

    caption = query.edit_message_caption.await_args.kwargs["caption"]
    assert caption == i18n.t("profile.edit.none_achievements", "EN")


async def test_ticking_a_season_pins_its_run_and_redraws(session_factory) -> None:
    season = _season(session_factory, "demo_1", 0)
    query = _query(cb.toggle_data(ME, STAMP, season, 0))
    await _tap(session_factory, query)

    with session_scope(session_factory) as session:
        assert showcase.picks(session, ME, STAMP) == ["demo_1"]
    assert _buttons(query)[1][0][0] == edit.SHOWN + "Demo Season"
    query.answer.assert_awaited_once_with()


async def test_ticking_an_achievement_past_the_limit_alerts(session_factory) -> None:
    grants = [_grant(session_factory, f"k{i}") for i in range(showcase.BADGES_MAX + 1)]
    with session_scope(session_factory) as session:
        for grant in grants[:-1]:
            showcase.toggle(session, ME, ACH, str(grant))
    query = _query(cb.toggle_data(ME, ACH, grants[-1], 0))
    await _tap(session_factory, query)

    expected = i18n.t("profile.edit.full", "EN", limit=showcase.BADGES_MAX)
    query.answer.assert_awaited_once_with(expected, show_alert=True)
    query.edit_message_caption.assert_not_awaited()


async def test_ticking_a_season_that_is_gone_alerts(session_factory, log_records) -> None:
    query = _query(cb.toggle_data(ME, STAMP, 999, 0))
    await _tap(session_factory, query)

    query.answer.assert_awaited_once_with(i18n.t("profile.edit.gone", "EN"), show_alert=True)
    assert any(r.level == "WARNING" and r.extra.get("ref_id") == 999 for r in log_records)


@pytest.mark.parametrize("data", [cb.edit_data(ME, STAMP, 0), cb.toggle_data(ME, STAMP, 1, 0)])
async def test_the_editor_is_refused_outside_a_dm(session_factory, data, log_records) -> None:
    _season(session_factory, "demo_1", 0)
    query = _query(data, chat_type=ChatType.SUPERGROUP)
    await _tap(session_factory, query)

    query.edit_message_caption.assert_not_awaited()
    query.answer.assert_awaited_once_with()
    with session_scope(session_factory) as session:
        assert showcase.picks(session, ME, STAMP) == []
    assert any(r.level == "WARNING" and "private chat" in r.message for r in log_records)


async def test_done_shows_the_card_again_with_the_edit_button(session_factory, monkeypatch) -> None:
    monkeypatch.setattr(card_module, "fetch_avatar", AsyncMock(return_value=None))
    query = _query(cb.card_data(ME, ME, "m"))
    await _tap(session_factory, query)

    markup = query.edit_message_media.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[-1][0].callback_data == cb.edit_data(ME, STAMP, 0)
