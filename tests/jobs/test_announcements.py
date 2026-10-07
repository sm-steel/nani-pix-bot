from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.orm import Session, sessionmaker
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import announcements
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import settings
from nani_pix_bot.services.achievements import engine, outbox
from nani_pix_bot.services.quiet_hours import QuietHours

pytestmark = pytest.mark.achievements


def _context(session_factory: sessionmaker[Session]) -> MagicMock:
    context = MagicMock()
    context.job = None
    context.bot.send_message = AsyncMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    return context


def _grant(session_factory: sessionmaker[Session], key: str = "kingmaker") -> None:
    with session_scope(session_factory) as session:
        session.add(Player(telegram_user_id=1, username="alice"))
        session.flush()
        engine.grant(session, engine.GrantRequest(1, key))


async def _drain(context: MagicMock) -> None:
    await announcements.drain_outbox(cast(ContextTypes.DEFAULT_TYPE, context))


async def test_an_unlock_is_posted_once_into_the_game_topic(session_factory) -> None:
    _grant(session_factory)
    context = _context(session_factory)

    await _drain(context)
    await _drain(context)

    context.bot.send_message.assert_awaited_once()
    kwargs = context.bot.send_message.await_args.kwargs
    assert (kwargs["chat_id"], kwargs["message_thread_id"]) == (555, 7)
    assert "@alice" in kwargs["text"]
    assert "Kingmaker" in kwargs["text"]


async def test_cascaded_unlocks_post_in_grant_order(session_factory) -> None:
    _grant(session_factory, "pioneer")
    context = _context(session_factory)

    await _drain(context)

    texts = [c.kwargs["text"] for c in context.bot.send_message.await_args_list]
    assert "Pioneer" in texts[0]
    assert "Pixel Magnate" in texts[1]


async def test_nothing_is_posted_during_quiet_hours(session_factory, quiet_now: QuietHours) -> None:
    _grant(session_factory)
    with session_scope(session_factory) as session:
        settings.set_quiet_hours(session, quiet_now)
    context = _context(session_factory)

    await _drain(context)

    context.bot.send_message.assert_not_awaited()
    with session_scope(session_factory) as session:
        assert len(outbox.pending(session, 10)) == 1


async def test_a_failing_post_gives_up_after_three_attempts(session_factory, records) -> None:
    _grant(session_factory)
    context = _context(session_factory)
    context.bot.send_message.side_effect = TelegramError("boom")

    for _ in range(outbox.MAX_ATTEMPTS + 1):
        await _drain(context)

    assert context.bot.send_message.await_count == outbox.MAX_ATTEMPTS
    assert any(level == "ERROR" and "giving up" in message for level, message in records)
