from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.orm import Session
from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes

from nani_pix_bot.commands import leaderboard
from nani_pix_bot.commands.helpers.rich import md_escape
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.achievement import AchievementGrant
from nani_pix_bot.models.enums import Rarity
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import i18n


def _make_update(*, thread_id: int | None = 7, chat_type: str = "supergroup") -> MagicMock:
    update = MagicMock()
    update.effective_user.id = 1
    update.effective_chat.id = 555
    update.effective_chat.type = chat_type
    update.message.chat_id = 555
    update.message.message_thread_id = thread_id
    update.effective_message = update.message
    update.message.reply_text = AsyncMock()
    return update


def _make_context(session_factory, *, member: bool = True) -> MagicMock:
    context = MagicMock()
    status = ChatMemberStatus.MEMBER if member else ChatMemberStatus.LEFT
    context.bot.get_chat_member = AsyncMock(return_value=MagicMock(status=status))
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    return context


async def _run(update: MagicMock, context: MagicMock) -> AsyncMock:
    with patch.object(leaderboard, "send_rich", new=AsyncMock()) as sent:
        await leaderboard.leaderboard_command(
            cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
        )
    return sent


async def test_ignored_outside_the_game_topic(session_factory) -> None:
    sent = await _run(_make_update(thread_id=999), _make_context(session_factory))

    sent.assert_not_awaited()


async def test_a_table_of_wins_currency_and_achievement_points(session_factory) -> None:
    with session_scope(session_factory) as session:
        session.add_all(
            [
                Player(telegram_user_id=1, username="low", wins=1),
                Player(telegram_user_id=2, username="high_one", wins=5, currency=77),
            ]
        )
        session.flush()
        session.add(
            AchievementGrant(
                player_id=2, key="clutch", tier=1, rarity=Rarity.GOLD, reward=0, points=6
            )
        )

    sent = await _run(_make_update(), _make_context(session_factory))

    _bot, target, markdown, _markup = sent.await_args_list[0].args
    assert (target.chat_id, target.thread_id) == (555, 7)
    assert "| # | Player | 👑 | 💠 | 🏆 |" in markdown
    assert f"| 1 | {md_escape('@high_one')} | 5 | 77 | 6 |" in markdown
    assert markdown.index("high") < markdown.index("low")
    assert "You:" not in markdown  # the topic copy is shared


async def test_says_so_when_no_one_has_won(session_factory) -> None:
    sent = await _run(_make_update(), _make_context(session_factory))

    assert md_escape(i18n.t("leaderboard.empty", "en")) in sent.await_args_list[0].args[2]


async def test_a_chosen_title_shows_next_to_the_name(session_factory) -> None:
    with session_scope(session_factory) as session:
        session.add(Player(telegram_user_id=1, username="alice", wins=3, title_key="pioneer:1:"))

    sent = await _run(_make_update(), _make_context(session_factory))

    assert md_escape("@alice «Pioneer»") in sent.await_args_list[0].args[2]


def _crowd(session: Session, count: int) -> None:
    session.add_all(Player(telegram_user_id=i, wins=100 - i) for i in range(2, count + 2))
    session.add(Player(telegram_user_id=1, username="me", wins=1))
    session.flush()


async def test_in_dm_it_pages_and_adds_your_own_line(session_factory) -> None:
    with session_scope(session_factory) as session:
        _crowd(session, 12)

    sent = await _run(
        _make_update(thread_id=None, chat_type="private"), _make_context(session_factory)
    )

    _bot, _target, markdown, markup = sent.await_args_list[0].args
    assert markdown.count("\n| ") - 1 == leaderboard.LEADERBOARD_SIZE
    assert md_escape("You: #13 · 1 👑 · 0 💠 · 0 🏆") in markdown
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert "1/2" in labels


async def test_a_non_member_is_refused_in_dm(session_factory) -> None:
    update = _make_update(thread_id=None, chat_type="private")

    sent = await _run(update, _make_context(session_factory, member=False))

    sent.assert_not_awaited()
    update.message.reply_text.assert_awaited_once_with(i18n.t("dm_start.not_a_member", "en"))


async def test_paging_edits_the_message(session_factory, log_records) -> None:
    with session_scope(session_factory) as session:
        _crowd(session, 12)
    update = MagicMock()
    update.callback_query.data = leaderboard.page_data(1)
    update.callback_query.answer = AsyncMock()
    update.callback_query.message.chat.type = "supergroup"
    update.callback_query.message.chat.id = 555
    update.callback_query.message.message_id = 9

    with patch.object(leaderboard, "edit_rich", new=AsyncMock()) as edited:
        await leaderboard.leaderboard_callback(
            cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _make_context(session_factory))
        )

    markdown = edited.await_args_list[0].args[2]
    assert "\n| 11 | " in markdown
    assert any(r.message == "paged the leaderboard to page 2" for r in log_records)


def test_page_data_round_trips_and_rejects_junk() -> None:
    assert leaderboard.parse_page(leaderboard.page_data(4)) == 4
    for junk in ("lb:", "lb:x", "lb:1:2", "xx:1", f"lb:{2**70}"):
        assert leaderboard.parse_page(junk) is None
