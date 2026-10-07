from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.orm import Session
from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes

from nani_pix_bot.commands import standings
from nani_pix_bot.commands.helpers.rich import md_escape
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import EventType, PeriodType
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import events, i18n
from nani_pix_bot.services.achievements.periods import Standing

VIEWER = 6
BYSTANDER = 7


def _board(count: int) -> list[Standing]:
    return [Standing(player_id=i, score=100 - i, wins=1) for i in range(1, count + 1)]


def test_you_line_is_absent_for_someone_inside_the_top_five() -> None:
    assert standings.you_line(_board(8), 5, "en") is None


def test_you_line_shows_rank_score_and_wins_outside_the_top_five() -> None:
    board = _board(8)
    board[5].wins = 3

    line = standings.you_line(board, 6, "en")

    assert line == "You: #6 · 94 🌟 · 3 👑"


def test_you_line_says_so_when_the_viewer_has_not_scored() -> None:
    assert standings.you_line(_board(3), 99, "en") == i18n.t("standings.you_unscored", "en")


def test_top_rows_are_cut_at_five() -> None:
    assert len(standings.top_rows(_board(9))) == standings.TOP_SIZE == 5


def _win(session: Session, winner: int, game: int, stage: int, at: datetime) -> None:
    events.emit(
        session,
        EventType.GAME_WON,
        events.Involved(actor_id=winner, game_id=game),
        stage=stage,
        hard_mode=False,
        how="guess",
        pot=0,
        seconds=None,
        last_slot=False,
        distinct_guessers=1,
        winner_wrong=0,
        first_guess=False,
        ended_at=at.isoformat(),
    )


def _seed(session_factory) -> None:
    now = datetime.now(UTC)
    with session_scope(session_factory) as session:
        session.add_all(
            Player(telegram_user_id=i, username=f"p_{i}" if i != 2 else "kurogane_42")
            for i in range(1, 8)
        )
        session.flush()
        for player in range(1, 6):
            _win(session, player, player, 1, now)  # 5 each
        _win(session, VIEWER, 6, 5, now)  # 1 point, outside the top five


def _update(user_id: int, thread_id: int | None = 7, chat_type: str = "supergroup") -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.id = 555
    update.effective_chat.type = chat_type
    update.message.message_thread_id = thread_id
    update.message.chat_id = 555
    update.effective_message = update.message
    return update


def _context(session_factory, *, member: bool = True) -> MagicMock:
    context = MagicMock()
    context.args = []
    joined = ChatMemberStatus.MEMBER if member else ChatMemberStatus.LEFT
    context.bot.get_chat_member = AsyncMock(return_value=MagicMock(status=joined))
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    return context


async def _run(update: MagicMock, session_factory) -> AsyncMock:
    with patch.object(standings, "send_rich", new=AsyncMock()) as sent:
        await standings.standings_command(
            cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _context(session_factory))
        )
    return sent


async def test_posts_week_month_and_year_in_order_with_tables(session_factory) -> None:
    _seed(session_factory)

    sent = await _run(_update(VIEWER), session_factory)

    sent.assert_awaited_once()
    _bot, target, markdown = sent.await_args_list[0].args
    assert (target.chat_id, target.thread_id) == (555, 7)
    headers = [line for line in markdown.splitlines() if line.startswith("## ")]
    assert len(headers) == 3
    assert headers[0].startswith("## 🌟 Week ")
    assert "ends" in headers[0]
    assert str(datetime.now(UTC).year) in headers[2]
    assert markdown.index(headers[0]) < markdown.index(headers[1]) < markdown.index(headers[2])
    assert markdown.count("| # | Player | 🌟 | 👑 |") == 3
    # top five only, names escaped, viewer's own line outside the top five
    assert markdown.count("\n| 1 | ") == 3
    assert markdown.count(r"@kurogane\_42") == 3
    assert "\n| 6 | " not in markdown
    assert markdown.count(r"You: \#6 · 1 🌟 · 1 👑") == 3


async def test_a_viewer_without_a_score_gets_the_unscored_line(session_factory) -> None:
    _seed(session_factory)

    sent = await _run(_update(BYSTANDER), session_factory)

    markdown = sent.await_args_list[0].args[2]
    assert markdown.count(md_escape(i18n.t("standings.you_unscored", "en"))) == 3


async def test_an_empty_period_says_nobody_has_won(session_factory) -> None:
    sent = await _run(_update(VIEWER), session_factory)

    markdown = sent.await_args_list[0].args[2]
    assert markdown.count(md_escape(i18n.t("standings.empty", "en"))) == 3
    assert "| # |" not in markdown
    assert "You:" not in markdown


async def test_works_in_a_private_chat(session_factory) -> None:
    sent = await _run(_update(VIEWER, thread_id=None, chat_type="private"), session_factory)

    sent.assert_awaited_once()


async def test_ignored_outside_the_game_topic(session_factory) -> None:
    sent = await _run(_update(VIEWER, thread_id=99), session_factory)

    sent.assert_not_awaited()


def test_period_types_are_covered_in_order() -> None:
    assert standings.PERIODS == (PeriodType.WEEK, PeriodType.MONTH, PeriodType.YEAR)


async def test_a_non_member_is_refused_in_a_private_chat(session_factory, records) -> None:
    update = _update(BYSTANDER, thread_id=None, chat_type="private")
    update.message.reply_text = AsyncMock()
    context = _context(session_factory, member=False)

    with patch.object(standings, "send_rich", new=AsyncMock()) as sent:
        await standings.standings_command(
            cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
        )

    sent.assert_not_awaited()
    update.message.reply_text.assert_awaited_once_with(i18n.t("dm_start.not_a_member", "en"))
    assert any(level == "WARNING" and "not a group member" in m for level, m in records)
