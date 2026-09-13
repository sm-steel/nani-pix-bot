from unittest.mock import AsyncMock, MagicMock

import pytest

from nani_pix_bot import heartbeat


def _make_application(get_updates_mock: AsyncMock) -> MagicMock:
    application = MagicMock()
    application.bot.get_updates = get_updates_mock
    return application


async def test_install_touches_the_heartbeat_immediately() -> None:
    heartbeat_path = MagicMock()
    application = _make_application(AsyncMock(return_value=[]))

    heartbeat.install(application, heartbeat_path=heartbeat_path)

    heartbeat_path.touch.assert_called_once()


async def test_install_touches_the_heartbeat_again_after_a_successful_call() -> None:
    heartbeat_path = MagicMock()
    application = _make_application(AsyncMock(return_value=[]))
    heartbeat.install(application, heartbeat_path=heartbeat_path)

    await application.bot.get_updates(offset=0)

    assert heartbeat_path.touch.call_count == 2  # the install-time touch, plus this one


async def test_install_does_not_touch_the_heartbeat_when_the_call_fails() -> None:
    # This is the whole point: a failing getUpdates must let the file go
    # stale, not keep refreshing it — a periodic JobQueue-based heartbeat
    # would get this wrong (see module docstring).
    heartbeat_path = MagicMock()
    application = _make_application(AsyncMock(side_effect=RuntimeError("boom")))
    heartbeat.install(application, heartbeat_path=heartbeat_path)

    with pytest.raises(RuntimeError, match="boom"):
        await application.bot.get_updates(offset=0)

    heartbeat_path.touch.assert_called_once()  # only the install-time touch — not a second one


async def test_install_preserves_call_args_and_return_value() -> None:
    heartbeat_path = MagicMock()
    get_updates_mock = AsyncMock(return_value=["update-1"])
    application = _make_application(get_updates_mock)
    heartbeat.install(application, heartbeat_path=heartbeat_path)

    result = await application.bot.get_updates(offset=5, timeout=10)

    assert result == ["update-1"]
    get_updates_mock.assert_awaited_once_with(offset=5, timeout=10)
