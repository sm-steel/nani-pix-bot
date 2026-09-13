"""Tests for app.py's wiring. build_application() itself is exercised
directly (no network calls happen building an Application/registering
handlers) — main()'s actual polling loop is not something a unit test
should run."""

import re
from typing import cast
from unittest.mock import MagicMock

import pytest
from telegram.error import Conflict, NetworkError
from telegram.ext import CallbackQueryHandler, ContextTypes

from nani_pix_bot import app
from nani_pix_bot.commands.dm_start import pick_callback_handler
from nani_pix_bot.config import Config

_VALID_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"  # noqa: S105 - test fixture, not a real token


def _config(**overrides) -> Config:
    defaults = {
        "bot_token": _VALID_TOKEN,
        "group_chat_id": -100555,
        "game_topic_id": 7,
        "admin_user_ids": [],
        "log_level": "INFO",
        "telegram_proxy_url": None,
        "tmdb_read_access_token": None,
        "database_url": "sqlite:///:memory:",
    }
    defaults.update(overrides)
    return Config(**defaults)


def test_build_application_populates_bot_data() -> None:
    application = app.build_application(_config())

    assert application.bot_data["group_chat_id"] == -100555
    assert application.bot_data["game_topic_id"] == 7
    assert application.bot_data["session_factory"] is not None
    assert application.bot_data["search_client"] is not None


def test_build_application_registers_every_command() -> None:
    application = app.build_application(_config())

    registered_commands = {
        command
        for group in application.handlers.values()
        for handler in group
        for command in getattr(handler, "commands", [])
    }

    assert registered_commands == {
        "guess",
        "correct",
        "skip",
        "stop",
        "leaderboard",
        "language",
        "start",
        "help",
        "newgame",
        "stageconfig",
        "setstageconfig",
        "setstage",
        "setgamesenabled",
        "testpixels",  # TEMPORARY — see commands/testpixels.py
    }


def test_pick_callback_handler_pattern_matches_every_providers_prefix() -> None:
    # Regression: jikan/tmdb were added to the search/pick flow (and
    # their own *_pick: keyboard prefixes) without this pattern being
    # updated, so tapping a Jikan/TMDB result button silently did
    # nothing — the callback query never reached the handler at all.
    application = app.build_application(_config())
    handlers = [
        handler
        for group in application.handlers.values()
        for handler in group
        if isinstance(handler, CallbackQueryHandler) and handler.callback is pick_callback_handler
    ]
    assert len(handlers) == 1
    pattern = handlers[0].pattern
    assert isinstance(pattern, re.Pattern)
    pick_examples = (
        "anilist_pick:99",
        "shikimori_pick:1",
        "jikan_pick:1",
        "tmdb_pick:1",
        "anilist_retry",
    )
    for data in pick_examples:
        assert pattern.match(data), f"{data!r} should match the pick-callback pattern"


def test_build_application_succeeds_with_a_proxy_configured() -> None:
    # Exercises ApplicationBuilder().proxy()/.get_updates_proxy() without
    # inspecting httpx/PTB internals, which are private and version-fragile.
    application = app.build_application(_config(telegram_proxy_url="http://user:pass@host:8888"))

    assert application.bot.token == _VALID_TOKEN


def test_build_application_configures_a_larger_get_updates_pool() -> None:
    # Same "don't inspect PTB internals" precedent as the proxy test above
    # — HTTPXRequest doesn't expose connection_pool_size as a public
    # attribute, so this just proves .get_updates_connection_pool_size()
    # is a real builder method that doesn't blow up, not the resulting
    # pool's actual behavior (see issue #51).
    application = app.build_application(_config())

    assert application.bot.token == _VALID_TOKEN


def test_build_application_registers_an_error_handler() -> None:
    application = app.build_application(_config())

    assert len(application.error_handlers) == 1


async def test_error_handler_logs_network_errors_as_a_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings = []
    monkeypatch.setattr(app.logger, "warning", lambda *args: warnings.append(args))
    context = MagicMock()
    context.error = NetworkError("connection reset")

    await app._error_handler(cast(object, "some update"), cast(ContextTypes.DEFAULT_TYPE, context))

    assert warnings


async def test_error_handler_logs_a_conflict_as_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    # A 409 Conflict self-heals on its own (see the 2026-09-13 incident) —
    # it's not a bug, so it shouldn't be logged as one.
    warnings = []
    monkeypatch.setattr(app.logger, "warning", lambda *args: warnings.append(args))
    context = MagicMock()
    context.error = Conflict("terminated by other getUpdates request")

    await app._error_handler(cast(object, "some update"), cast(ContextTypes.DEFAULT_TYPE, context))

    assert warnings


async def test_error_handler_logs_anything_else_as_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    errors = []
    fake_opt = lambda **kwargs: MagicMock(error=lambda *a: errors.append(a))  # noqa: E731
    monkeypatch.setattr(app.logger, "opt", fake_opt)
    context = MagicMock()
    context.error = ValueError("a real bug")

    await app._error_handler(cast(object, "some update"), cast(ContextTypes.DEFAULT_TYPE, context))

    assert errors
