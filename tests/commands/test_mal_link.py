"""Tests for commands.mal_link — hardcoded test token values trigger an S106
warning (Possible hardcoded password), which is a false positive in test data."""
# ruff: noqa: S106

from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from cryptography.fernet import Fernet
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands import mal_link as mal_link_commands
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import mal_link

_ENCRYPTION_KEY = Fernet.generate_key().decode()


def _make_mal_bot_data(
    session_factory, *, mal_client_id="cid", mal_redirect_uri="https://example.com/cb"
) -> dict:
    """Matches Task 6's actual wiring — individual bot_data keys, not a
    nested `config` object (this codebase never stores the whole Config
    in bot_data)."""
    return {
        "session_factory": session_factory,
        "mal_client_id": mal_client_id,
        "mal_client_secret": "csecret",
        "mal_redirect_uri": mal_redirect_uri,
        "mal_token_encryption_key": _ENCRYPTION_KEY,
    }


def _make_update(*, user_id: int = 1) -> MagicMock:
    update = MagicMock(spec=Update)
    update.effective_user = MagicMock(id=user_id)
    update.effective_chat = MagicMock(type="private")
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    return update


async def test_linkmal_replies_with_an_authorize_url_and_schedules_expiry(session_factory) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.commit()

    update = _make_update()
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.bot_data = _make_mal_bot_data(session_factory)
    context.job_queue = MagicMock()

    await mal_link_commands.linkmal_command(cast(Update, update), context)

    update.message.reply_text.assert_awaited_once()
    reply_text = update.message.reply_text.await_args.args[0]
    assert "myanimelist.net/v1/oauth2/authorize" in reply_text
    context.job_queue.run_once.assert_called_once()

    with session_factory() as session:
        pending = mal_link.get_pending_link(session, 1)
        assert pending is not None


async def test_linkmal_replies_with_not_configured_when_mal_is_unset(session_factory) -> None:
    update = _make_update()
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.bot_data = _make_mal_bot_data(
        session_factory, mal_client_id=None, mal_redirect_uri=None
    )
    context.job_queue = MagicMock()

    await mal_link_commands.linkmal_command(cast(Update, update), context)

    update.message.reply_text.assert_awaited_once()
    reply_text = update.message.reply_text.await_args.args[0]
    assert "not" in reply_text.lower() or "unavailable" in reply_text.lower()
    context.job_queue.run_once.assert_not_called()


async def test_linkmal_ignores_group_chats(session_factory) -> None:
    update = _make_update()
    update.effective_chat = MagicMock(type="group")
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.bot_data = _make_mal_bot_data(session_factory)
    context.job_queue = MagicMock()

    await mal_link_commands.linkmal_command(cast(Update, update), context)

    update.message.reply_text.assert_not_called()
    context.job_queue.run_once.assert_not_called()


async def test_unlinkmal_deletes_credentials_and_confirms(session_factory) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        mal_link.upsert_credentials(
            session,
            1,
            data=mal_link.CredentialsData(
                encryption_key=_ENCRYPTION_KEY,
                access_token="real-access-token",
                refresh_token="real-refresh-token",
                expires_at=datetime.now(UTC),
                mal_username=None,
            ),
        )
        session.commit()

    update = _make_update()
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.bot_data = {"session_factory": session_factory}

    await mal_link_commands.unlinkmal_command(cast(Update, update), context)

    update.message.reply_text.assert_awaited_once()
    with session_factory() as session:
        assert mal_link.get_credentials(session, 1, encryption_key=_ENCRYPTION_KEY) is None


async def test_unlinkmal_ignores_group_chats(session_factory) -> None:
    update = _make_update()
    update.effective_chat = MagicMock(type="group")
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.bot_data = {"session_factory": session_factory}

    await mal_link_commands.unlinkmal_command(cast(Update, update), context)

    update.message.reply_text.assert_not_called()
