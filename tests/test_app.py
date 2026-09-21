"""Tests for app.py's wiring. build_application() itself is exercised
directly (no network calls happen building an Application/registering
handlers) — main()'s actual polling loop is not something a unit test
should run."""

import re
from typing import cast
from unittest.mock import MagicMock

import pytest
from telegram.error import Conflict, NetworkError
from telegram.ext import CallbackQueryHandler, ContextTypes, TypeHandler

from nani_pix_bot import app
from nani_pix_bot.commands.dm_start import (
    pick_callback_handler,
    screenshot_gallery_callback_handler,
    screenshot_search_again_callback_handler,
    screenshot_search_pick_callback_handler,
    screenshot_source_callback_handler,
    screenshot_upload_instead_callback_handler,
)
from nani_pix_bot.commands.helpers import player_tracking
from nani_pix_bot.config import Config
from nani_pix_bot.models.enums import Provider

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
        "mal_client_id": None,
        "mal_client_secret": None,
        "mal_redirect_uri": None,
        "mal_token_encryption_key": None,
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
        "setautostart",
        "version",
    }


def test_pick_callback_handler_pattern_matches_every_providers_prefix() -> None:
    # Regression: tenrai/tmdb were added to the search/pick flow (and
    # their own *_pick: keyboard prefixes) without this pattern being
    # updated, so tapping a Tenrai/TMDB result button silently did
    # nothing — the callback query never reached the handler at all.
    #
    # Driven off Provider rather than a hand-written list of four, for
    # the same reason app.py now builds the pattern that way: a list
    # here would be one more copy of the vocabulary, and would go stale
    # in exactly the same silent way the pattern itself did.
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
    pick_examples = (*(f"{provider}_pick:1" for provider in Provider), "search_retry")
    for data in pick_examples:
        assert pattern.match(data), f"{data!r} should match the pick-callback pattern"


def test_screenshot_callback_handlers_match_only_their_own_prefix() -> None:
    # Regression, same class of bug as the pick-callback pattern test
    # above: five screenshot-flow callback prefixes are each registered
    # as their own CallbackQueryHandler with its own regex — a typo'd
    # or overlapping pattern would either silently swallow taps meant
    # for a different handler, or (as happened with tenrai/tmdb above)
    # never reach any handler at all, and nothing else would catch it.
    application = app.build_application(_config())
    handlers_by_callback = {
        handler.callback: handler
        for group in application.handlers.values()
        for handler in group
        if isinstance(handler, CallbackQueryHandler)
    }

    examples_by_handler = {
        screenshot_upload_instead_callback_handler: ["screenshot:upload"],
        screenshot_source_callback_handler: ["screenshot_source:shikimori"],
        screenshot_gallery_callback_handler: [
            "screenshot_pick:shikimori:0",
            "screenshot_more:shikimori:5",
        ],
        screenshot_search_again_callback_handler: ["screenshot_search_again:tmdb"],
        screenshot_search_pick_callback_handler: ["screenshot_search_pick:tmdb:209867"],
    }
    # handler.callback's static type is a bare Callable (no __name__
    # guarantee), so names are looked up through this dict rather than
    # calling .__name__ on it directly — built from our own literal,
    # concretely-typed function objects above, which do have one.
    names = {callback: callback.__name__ for callback in examples_by_handler}
    for callback in examples_by_handler:
        assert callback in handlers_by_callback, f"{names[callback]} isn't registered"

    for callback, examples in examples_by_handler.items():
        pattern = handlers_by_callback[callback].pattern
        assert isinstance(pattern, re.Pattern)
        for data in examples:
            assert pattern.match(data), f"{data!r} should match {names[callback]}'s pattern"
            for other_callback, other_handler in handlers_by_callback.items():
                if other_callback is callback:
                    continue
                other_pattern = other_handler.pattern
                if not isinstance(other_pattern, re.Pattern):
                    continue
                other_name = names.get(other_callback, repr(other_callback))
                assert not other_pattern.match(data), (
                    f"{data!r} (meant for {names[callback]}) also matches {other_name}'s pattern"
                )


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


def test_build_application_registers_player_tracking_before_the_commands() -> None:
    """Ordering is the whole point: a /correct that names a user the bot
    has just seen must find them already recorded, so the tracking
    handler has to run in an earlier group than the command handlers."""
    application = app.build_application(_config())

    tracking_handlers = [
        handler
        for handler in application.handlers[app._PLAYER_TRACKING_GROUP]
        if isinstance(handler, TypeHandler)
    ]
    assert len(tracking_handlers) == 1
    assert tracking_handlers[0].callback is player_tracking.remember_user

    command_groups = {
        group
        for group, handlers in application.handlers.items()
        for handler in handlers
        if getattr(handler, "commands", None)
    }
    assert command_groups
    assert min(command_groups) > app._PLAYER_TRACKING_GROUP


async def test_post_shutdown_closes_all_three_search_clients() -> None:
    """They're process-lifetime objects in production, but every test
    that builds an Application leaks three of them otherwise."""
    application = app.build_application(_config())
    search_client = application.bot_data["search_client"]
    tmdb_client = application.bot_data["tmdb_client"]
    tenrai_client = application.bot_data["tenrai_client"]

    await app._post_shutdown(application)

    assert search_client.is_closed
    assert tmdb_client.is_closed
    assert tenrai_client.is_closed


def test_build_application_warns_once_when_the_tmdb_token_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The token is optional by design, but without this a fresh clone
    just shows an identification method that can only ever 401."""
    warnings = []
    monkeypatch.setattr(app.logger, "warning", lambda *args: warnings.append(args))

    app.build_application(_config(tmdb_read_access_token=None))

    assert len(warnings) == 1
    assert "TMDB_READ_ACCESS_TOKEN" in warnings[0][0]


def test_build_application_says_nothing_about_a_tmdb_token_that_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No warning when it's configured — and the repo is public, so no
    log line may ever carry the token itself either."""
    logged = []
    for level in ("debug", "info", "warning", "error"):
        monkeypatch.setattr(app.logger, level, lambda *args: logged.append(args))
    token = "tmdb-token-placeholder"  # noqa: S105 - test fixture, not a real token

    app.build_application(_config(tmdb_read_access_token=token))

    assert not logged
