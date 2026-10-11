from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import Forbidden
from telegram.ext import ContextTypes

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import announcements
from nani_pix_bot.models.enums import OutboxKind, SeasonStatus
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.services.achievements import outbox

NOW = datetime(2026, 11, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def fake_runs(monkeypatch):
    runs = registry.discover("tests.seasons.fake_runs")
    monkeypatch.setattr(registry, "all_runs", lambda: runs)


@pytest.fixture
def context(session_factory) -> MagicMock:
    context = MagicMock()
    context.job = None
    context.bot.send_message = AsyncMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    return context


def _queue(session_factory, *kinds: OutboxKind) -> None:
    with session_scope(session_factory) as session:
        session.add(Player(telegram_user_id=1, username="alice"))
        season = SeasonSchedule(
            run_id="demo_1",
            start_at=NOW - timedelta(days=1),
            end_at=NOW + timedelta(days=9),
            status=SeasonStatus.ACTIVE,
            created_by=9,
        )
        session.add(season)
        session.flush()
        for kind in kinds:
            if kind is OutboxKind.SEASON_TIER:
                granted = [
                    {"tier": 1, "premium": False, "new": True},
                    {"tier": 2, "premium": False, "new": False},
                ]
                outbox.enqueue_tier(session, season.id, 1, granted)
            else:
                outbox.enqueue_pass_done(session, season.id, 1)


async def _drain(context: MagicMock) -> None:
    await announcements.drain_outbox(cast(ContextTypes.DEFAULT_TYPE, context))


async def test_a_tier_notice_goes_to_the_players_dm(session_factory, context) -> None:
    _queue(session_factory, OutboxKind.SEASON_TIER)
    await _drain(context)
    kwargs = context.bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == 1
    assert "message_thread_id" not in kwargs
    assert "Tier 1" in kwargs["text"]
    assert "(already yours)" not in kwargs["text"].splitlines()[1]
    assert "(already yours)" in kwargs["text"]


async def test_a_dm_the_bot_cannot_send_is_given_up_without_blocking(
    session_factory, context, log_records
) -> None:
    _queue(session_factory, OutboxKind.SEASON_TIER, OutboxKind.SEASON_COMPLETE)
    context.bot.send_message.side_effect = [Forbidden("bot can't initiate"), None]
    await _drain(context)
    with session_scope(session_factory) as session:
        assert outbox.pending(session, 10) == []  # both settled
    assert context.bot.send_message.await_count == 2
    assert any(r.level == "WARNING" and "DM" in r.message for r in log_records)
    posted = [r for r in log_records if r.level == "INFO" and "posted announcement" in r.message]
    assert len(posted) == 1  # only the pass-done line that really went out
    assert len(posted[0].extra["outbox_ids"]) == 1


async def test_pass_done_is_a_topic_line(session_factory, context) -> None:
    _queue(session_factory, OutboxKind.SEASON_COMPLETE)
    await _drain(context)
    kwargs = context.bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == context.bot_data["group_chat_id"]
    assert kwargs["message_thread_id"] == 7
    assert "@alice" in kwargs["text"]
