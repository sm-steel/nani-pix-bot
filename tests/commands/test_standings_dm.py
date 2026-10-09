from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session
from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes

from nani_pix_bot.commands import standings, standings_dm
from nani_pix_bot.commands.helpers.rich import md_escape
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import EventType
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import events, i18n
from nani_pix_bot.services.achievements import periods

UTC_TZ = ZoneInfo("UTC")
HOST = 9


def _win(session: Session, winner: int, game: int, stage: int, at: datetime, **extra) -> None:
    facts = {
        "stage": stage,
        "hard_mode": False,
        "how": "guess",
        "pot": 0,
        "seconds": None,
        "last_slot": False,
        "distinct_guessers": 1,
        "winner_wrong": 0,
        "first_guess": False,
        "ended_at": at.isoformat(),
    } | extra
    events.emit(
        session,
        EventType.GAME_WON,
        events.Involved(actor_id=winner, subject_id=HOST, game_id=game),
        **facts,
    )


def _players(session: Session, *ids: int) -> None:
    session.add_all(Player(telegram_user_id=i, username=f"p_{i}") for i in ids)
    session.flush()


def test_the_rules_spell_out_every_point_value_from_the_constants() -> None:
    for lang in ("en", "ru"):
        text = standings_dm.rules_markdown(lang, "Europe/Moscow")
        for stage, points in enumerate(periods.WIN_POINTS, start=1):
            assert f"| {stage}/{len(periods.WIN_POINTS)} | {points} |" in text
        for points in periods.HARD_POINTS:
            assert f"{points} 🌟" in text
        assert f"+{periods.HOST_POINTS} 🌟" in text
        assert md_escape("Europe/Moscow") in text
        assert "standings.rules" not in text  # every key resolved
        clean = i18n.t("standings.rules.clean", lang, bonus=periods.CLEAN_BONUS)
        assert md_escape(clean) in text
        assert f"+{periods.CLEAN_BONUS} 🌟" in text
        ties = md_escape(i18n.t("standings.rules.ties", lang))
        assert ties in text
        assert "❌" in ties
        assert "⏱" in ties


def test_the_feed_marks_a_clean_win(session: Session) -> None:
    _players(session, 1, 2, HOST)
    now = datetime.now(UTC)
    _win(session, 1, 1, 1, now - timedelta(minutes=2), winner_wrong=0)
    _win(session, 2, 2, 1, now - timedelta(minutes=1), winner_wrong=2)
    period = periods.period_at(periods.PeriodType.WEEK, now, UTC_TZ)

    markdown, _markup = standings_dm.feed_view(session, 0, period, UTC_TZ, "en")

    rows = [line for line in markdown.splitlines() if " | \\+" in line]
    clean = md_escape(i18n.t("standings.recent.clean", "en"))
    assert [clean in row for row in rows] == [False, False, False, True]
    assert f"\\+{periods.WIN_POINTS[0] + periods.CLEAN_BONUS}" in rows[3]


def test_the_feed_lists_shares_newest_first_with_rank_moves(session: Session) -> None:
    _players(session, 1, 2, HOST)
    now = datetime.now(UTC)
    _win(session, 1, 1, 4, now - timedelta(minutes=2))  # 1: 3 (#1), host: 1 (#2)
    _win(session, 2, 2, 1, now - timedelta(minutes=1))  # 2: 6 (#1), host 2 (#3)
    period = periods.period_at(periods.PeriodType.WEEK, now, UTC_TZ)

    markdown, _markup = standings_dm.feed_view(session, 0, period, UTC_TZ, "en")

    rows = [line for line in markdown.splitlines() if " | \\+" in line]
    assert len(rows) == 4
    assert "p\\_2" in rows[1]
    assert "\\+6" in rows[1]  # stage 1 plus the clean bonus
    assert "— → \\#1" in rows[1]  # player 2 entered at the top
    assert "\\#2 → \\#3" in rows[0]  # the host's second share pushed them down
    assert md_escape(i18n.t("standings.recent.host", "en", game=2)) in rows[0]


def test_the_feed_pages_by_ten(session: Session) -> None:
    _players(session, 1, HOST)
    now = datetime.now(UTC)
    for game in range(1, 7):  # 12 shares
        _win(session, 1, game, 1, now - timedelta(minutes=10 - game))
    period = periods.period_at(periods.PeriodType.WEEK, now, UTC_TZ)

    first, markup = standings_dm.feed_view(session, 0, period, UTC_TZ, "en")
    second, _ = standings_dm.feed_view(session, 1, period, UTC_TZ, "en")

    assert first.count("\n| ") - 1 == standings_dm.FEED_PAGE_SIZE  # minus the separator
    assert second.count("\n| ") - 1 == 2
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert "1/2" in labels


def test_an_empty_week_says_so(session: Session) -> None:
    period = periods.period_at(periods.PeriodType.WEEK, datetime.now(UTC), UTC_TZ)

    markdown, markup = standings_dm.feed_view(session, 0, period, UTC_TZ, "en")

    assert md_escape(i18n.t("standings.empty", "en")) in markdown
    assert markup.inline_keyboard == ()


def test_feed_callback_data_round_trips_and_rejects_junk() -> None:
    assert standings_dm.parse_page(standings_dm.feed_data(3)) == 3
    for junk in ("std:r:", "std:r:x", "std:r:1:2", "std:q:1", f"std:r:{2**70}"):
        assert standings_dm.parse_page(junk) is None


def _context(session_factory, *, bot_username: str | None = "nani_pix_bot") -> MagicMock:
    context = MagicMock()
    context.args = []
    member = MagicMock(status=ChatMemberStatus.MEMBER)
    context.bot.get_chat_member = AsyncMock(return_value=member)
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    if bot_username:
        context.bot_data["bot_username"] = bot_username
    return context


def _group_update() -> MagicMock:
    update = MagicMock()
    update.effective_user.id = 1
    update.effective_chat.id = 555
    update.effective_chat.type = "supergroup"
    update.message.message_thread_id = 7
    update.message.chat_id = 555
    update.effective_message = update.message
    return update


async def test_standings_carries_the_two_dm_buttons(session_factory) -> None:
    with patch.object(standings, "send_rich", new=AsyncMock()) as sent:
        await standings.standings_command(
            cast(Update, _group_update()),
            cast(ContextTypes.DEFAULT_TYPE, _context(session_factory)),
        )

    markup = sent.await_args_list[0].args[3]
    urls = [b.url for row in markup.inline_keyboard for b in row]
    assert urls == [
        "https://t.me/nani_pix_bot?start=rules",
        "https://t.me/nani_pix_bot?start=recent",
    ]


async def test_standings_has_no_buttons_before_the_bot_knows_its_name(session_factory) -> None:
    with patch.object(standings, "send_rich", new=AsyncMock()) as sent:
        await standings.standings_command(
            cast(Update, _group_update()),
            cast(ContextTypes.DEFAULT_TYPE, _context(session_factory, bot_username=None)),
        )

    assert sent.await_args_list[0].args[3] is None


async def test_paging_the_feed_edits_the_message(session_factory, log_records) -> None:
    with session_scope(session_factory) as session:
        _players(session, 1, HOST)
        _win(session, 1, 1, 1, datetime.now(UTC))
    update = MagicMock()
    update.callback_query.data = standings_dm.feed_data(0)
    update.callback_query.answer = AsyncMock()
    update.callback_query.from_user.id = 1
    update.callback_query.message.chat.id = 1
    update.callback_query.message.message_id = 42

    with patch.object(standings_dm, "edit_rich", new=AsyncMock()) as edited:
        await standings_dm.standings_callback(
            cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _context(session_factory))
        )

    edited.assert_awaited_once()
    assert edited.await_args_list[0].args[1].message_id == 42
    assert any(r.message.startswith("paged the recent standings") for r in log_records)


async def test_a_junk_feed_tap_is_answered_and_logged(session_factory, log_records) -> None:
    update = MagicMock()
    update.callback_query.data = "std:r:nope"
    update.callback_query.answer = AsyncMock()

    with patch.object(standings_dm, "edit_rich", new=AsyncMock()) as edited:
        await standings_dm.standings_callback(
            cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _context(session_factory))
        )

    edited.assert_not_awaited()
    update.callback_query.answer.assert_awaited_once()
    assert any(r.level == "WARNING" for r in log_records)
