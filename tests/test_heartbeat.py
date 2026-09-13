from unittest.mock import MagicMock

import pytest

from nani_pix_bot import heartbeat


class _FrozenBot:
    """Mimics python-telegram-bot's `TelegramObject.__setattr__` freeze
    on live instances (`bot.get_updates = ...` raises `AttributeError`
    after construction — confirmed straight from that class's source).
    The first version of `heartbeat.install()` patched the instance and
    crash-looped in production within seconds against a real bot; a
    bare `MagicMock()` here would never have caught that, since mocks
    don't enforce this. Any fix has to patch the *class* instead, and
    this fake is what proves it actually does."""

    def __setattr__(self, key: str, value: object) -> None:
        if not key.startswith("_"):
            raise AttributeError(f"Attribute `{key}` can't be set!")
        object.__setattr__(self, key, value)


def _make_application(get_updates_impl) -> MagicMock:
    # A fresh, unique class per call — so patching it in one test can't
    # bleed into another the way patching the real shared ExtBot class
    # would.
    bot_class = type("FakeBot", (_FrozenBot,), {"get_updates": get_updates_impl})
    application = MagicMock()
    application.bot = bot_class()
    return application


async def test_install_does_not_raise_against_a_frozen_bot_instance() -> None:
    # The whole point: this must not do `application.bot.get_updates = ...`.
    async def get_updates(self, *args, **kwargs):
        return []

    application = _make_application(get_updates)

    heartbeat.install(application, heartbeat_path=MagicMock())  # must not raise


async def test_install_touches_the_heartbeat_immediately() -> None:
    async def get_updates(self, *args, **kwargs):
        return []

    application = _make_application(get_updates)
    heartbeat_path = MagicMock()

    heartbeat.install(application, heartbeat_path=heartbeat_path)

    heartbeat_path.touch.assert_called_once()


async def test_install_touches_the_heartbeat_again_after_a_successful_call() -> None:
    async def get_updates(self, *args, **kwargs):
        return []

    application = _make_application(get_updates)
    heartbeat_path = MagicMock()
    heartbeat.install(application, heartbeat_path=heartbeat_path)

    await application.bot.get_updates(offset=0)

    assert heartbeat_path.touch.call_count == 2  # the install-time touch, plus this one


async def test_install_does_not_touch_the_heartbeat_when_the_call_fails() -> None:
    # This is the whole point: a failing getUpdates must let the file go
    # stale, not keep refreshing it — a periodic JobQueue-based heartbeat
    # would get this wrong (see module docstring).
    async def get_updates(self, *args, **kwargs):
        raise RuntimeError("boom")

    application = _make_application(get_updates)
    heartbeat_path = MagicMock()
    heartbeat.install(application, heartbeat_path=heartbeat_path)

    with pytest.raises(RuntimeError, match="boom"):
        await application.bot.get_updates(offset=0)

    heartbeat_path.touch.assert_called_once()  # only the install-time touch — not a second one


async def test_install_preserves_call_args_and_return_value() -> None:
    calls = []

    async def get_updates(self, *args, **kwargs):
        calls.append((args, kwargs))
        return ["update-1"]

    application = _make_application(get_updates)
    heartbeat.install(application, heartbeat_path=MagicMock())

    result = await application.bot.get_updates(offset=5, timeout=10)

    assert result == ["update-1"]
    assert calls == [((), {"offset": 5, "timeout": 10})]
