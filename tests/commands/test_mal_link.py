"""Tests for commands.mal_link — hardcoded test token values trigger an S106
warning (Possible hardcoded password), which is a false positive in test data."""
# ruff: noqa: S106

from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.fernet import Fernet
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands import mal_link as mal_link_commands
from nani_pix_bot.commands.helpers.mal_config import MAL_BOT_DATA_KEYS
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import i18n, mal_link

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


@pytest.mark.parametrize("missing_key", list(MAL_BOT_DATA_KEYS))
async def test_linkmal_refuses_a_partial_configuration(session_factory, missing_key: str) -> None:
    """Any one of the four missing means linking cannot finish, so it must
    never start. The dangerous case is MAL_TOKEN_ENCRYPTION_KEY: linkmal
    used to check only client id + redirect uri, so a player could
    complete a real OAuth consent at MyAnimeList and paste the code back
    — and storing the tokens then raised AttributeError, with their
    single-use authorization code already spent."""
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.commit()

    update = _make_update()
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.bot_data = _make_mal_bot_data(session_factory)
    context.bot_data[missing_key] = None
    context.job_queue = MagicMock()

    await mal_link_commands.linkmal_command(cast(Update, update), context)

    assert update.message.reply_text.await_args.args[0] == i18n.t("mal_link.not_configured", "en")
    context.job_queue.run_once.assert_not_called()
    with session_factory() as session:
        assert mal_link.get_pending_link(session, 1) is None


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


async def test_unlinkmal_also_clears_a_live_pending_link(session_factory) -> None:
    """Changing your mind mid-re-link has to leave nothing behind: a
    surviving pending row outranks everything in search_text_handler, so
    the player's next plain DM would be eaten as a pasted authorization
    code instead of being handled as what they typed."""
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        mal_link.upsert_pending_link(session, 1, state="s", code_verifier="v")
        session.commit()

    update = _make_update()
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.bot_data = {"session_factory": session_factory}

    await mal_link_commands.unlinkmal_command(cast(Update, update), context)

    with session_factory() as session:
        assert mal_link.get_pending_link(session, 1) is None


async def test_unlinkmal_ignores_group_chats(session_factory) -> None:
    update = _make_update()
    update.effective_chat = MagicMock(type="group")
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.bot_data = {"session_factory": session_factory}

    await mal_link_commands.unlinkmal_command(cast(Update, update), context)

    update.message.reply_text.assert_not_called()
