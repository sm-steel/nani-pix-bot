from typing import cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands import version as version_command_module
from nani_pix_bot.services import i18n, settings
from nani_pix_bot.services.search import cache


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def _make_update(*, chat_id: int = 555, thread_id: int | None = None) -> MagicMock:
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.message.message_thread_id = thread_id
    update.effective_message = update.message
    update.message.reply_text = AsyncMock()
    return update


def _make_context(session_factory, *, search_client: httpx.AsyncClient) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
        "search_client": search_client,
    }
    return context


_HTTP_OK = 200


def _github_client(body: str | None, *, status: int = _HTTP_OK) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if status != _HTTP_OK:
            return httpx.Response(status)
        return httpx.Response(200, json={"body": body})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_version_command_replies_with_version_and_rendered_notes(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(version_command_module.version, "installed_version", lambda: "1.0.2")
    client = _github_client("### Bug Fixes\n\n- fixed a thing")
    update = _make_update()
    context = _make_context(session_factory, search_client=client)

    await version_command_module.version_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )
    await client.aclose()

    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args.args[0]
    assert "1.0.2" in text
    assert "<b>Bug Fixes</b>" in text
    assert "• fixed a thing" in text
    assert update.message.reply_text.await_args.kwargs["parse_mode"] == "HTML"


async def test_version_command_falls_back_when_no_release_found(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(version_command_module.version, "installed_version", lambda: "0.1.0")
    client = _github_client(None, status=404)
    update = _make_update()
    context = _make_context(session_factory, search_client=client)

    await version_command_module.version_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )
    await client.aclose()

    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args.args[0]
    assert text == i18n.t("version.no_notes", "en", version="0.1.0")


async def test_version_command_works_in_the_group_topic_too(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(version_command_module.version, "installed_version", lambda: "1.0.2")
    client = _github_client("notes")
    update = _make_update(thread_id=7)
    context = _make_context(session_factory, search_client=client)

    await version_command_module.version_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )
    await client.aclose()

    update.message.reply_text.assert_awaited_once()


async def test_version_command_uses_the_configured_language(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(version_command_module.version, "installed_version", lambda: "0.1.0")
    client = _github_client(None, status=404)
    with session_factory() as session:
        settings.set_language(session, "ru")
        session.commit()
    update = _make_update()
    context = _make_context(session_factory, search_client=client)

    await version_command_module.version_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )
    await client.aclose()

    text = update.message.reply_text.await_args.args[0]
    assert text == i18n.t("version.no_notes", "ru", version="0.1.0")


async def test_version_command_ignores_an_update_with_no_message(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(version_command_module.version, "installed_version", lambda: "1.0.2")
    client = _github_client("notes")
    update = MagicMock()
    update.message = None
    context = _make_context(session_factory, search_client=client)

    await version_command_module.version_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )
    await client.aclose()
