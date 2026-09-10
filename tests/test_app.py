"""Tests for app.py's wiring. build_application() itself is exercised
directly (no network calls happen building an Application/registering
handlers) — main()'s actual polling loop is not something a unit test
should run."""

from nani_pix_bot import app
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
        "database_url": "sqlite:///:memory:",
    }
    defaults.update(overrides)
    return Config(**defaults)


def test_build_application_populates_bot_data() -> None:
    application = app.build_application(_config())

    assert application.bot_data["group_chat_id"] == -100555
    assert application.bot_data["game_topic_id"] == 7
    assert application.bot_data["session_factory"] is not None
    assert application.bot_data["anilist_client"] is not None


def test_build_application_registers_every_command() -> None:
    application = app.build_application(_config())

    registered_commands = {
        command
        for group in application.handlers.values()
        for handler in group
        for command in getattr(handler, "commands", [])
    }

    assert registered_commands == {"guess", "correct", "skip", "leaderboard", "language"}


def test_build_application_succeeds_with_a_proxy_configured() -> None:
    # Exercises ApplicationBuilder().proxy()/.get_updates_proxy() without
    # inspecting httpx/PTB internals, which are private and version-fragile.
    application = app.build_application(_config(telegram_proxy_url="http://user:pass@host:8888"))

    assert application.bot.token == _VALID_TOKEN
