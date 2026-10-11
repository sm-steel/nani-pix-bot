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
    assert "Demo Season" in await _run(session_factory, _update())


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
