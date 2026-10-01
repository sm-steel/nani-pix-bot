from datetime import time
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes

from nani_pix_bot.commands import quiet_hours as qh_module
from nani_pix_bot.services import players, settings
from nani_pix_bot.services.quiet_hours import QuietHours


def _context(session_factory, *, admin: bool = True, args: list[str] | None = None) -> MagicMock:
    context = MagicMock()
    context.bot_data = {"session_factory": session_factory, "group_chat_id": 555}
    status = ChatMemberStatus.ADMINISTRATOR if admin else ChatMemberStatus.MEMBER
    context.bot.get_chat_member = AsyncMock(return_value=MagicMock(status=status))
    context.args = args or []
    return context


def _update(*, user_id: int = 1) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.type = "private"
    update.message.reply_text = AsyncMock()
    return update


def _callback_update(data: str, *, user_id: int = 1) -> MagicMock:
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.from_user.id = user_id
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


def _reply_text(update: MagicMock) -> str:
    args, kwargs = update.message.reply_text.await_args
    return args[0] if args else kwargs["text"]


async def _run(handler, update: MagicMock, context: MagicMock) -> None:
    await handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))


async def test_quiethours_rejects_non_admins(session_factory) -> None:
    update = _update()
    context = _context(session_factory, admin=False, args=["23:00", "08:00"])
    await _run(qh_module.quiethours_command, update, context)
    update.message.reply_text.assert_awaited_once()
    with session_factory() as session:
        assert settings.get_quiet_hours(session) is None


async def test_quiethours_status_when_off(session_factory) -> None:
    update = _update()
    await _run(qh_module.quiethours_command, update, _context(session_factory))
    assert "/quiethours" in _reply_text(update)


async def test_quiethours_status_when_on_shows_local_and_utc(session_factory) -> None:
    with session_factory() as session:
        settings.set_quiet_hours(
            session, QuietHours(start=time(23, 0), end=time(8, 0), tz=ZoneInfo("Europe/Moscow"))
        )
        session.commit()
    update = _update()
    await _run(qh_module.quiethours_command, update, _context(session_factory))
    text = _reply_text(update)
    assert "23:00" in text
    assert "Europe/Moscow" in text
    assert "20:00" in text


async def test_quiethours_set_without_timezone_prompts_for_one(session_factory) -> None:
    update = _update()
    context = _context(session_factory, args=["23:00", "08:00"])
    await _run(qh_module.quiethours_command, update, context)
    kwargs = update.message.reply_text.await_args.kwargs
    buttons = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert "set_timezone:Europe/Moscow" in buttons
    with session_factory() as session:
        assert settings.get_quiet_hours(session) is None


async def test_quiethours_set_uses_the_admins_timezone(session_factory) -> None:
    with session_factory() as session:
        players.set_timezone(session, 1, ZoneInfo("Europe/Moscow"))
        session.commit()
    update = _update()
    context = _context(session_factory, args=["23:00", "08:00"])
    await _run(qh_module.quiethours_command, update, context)
    with session_factory() as session:
        assert settings.get_quiet_hours(session) == QuietHours(
            start=time(23, 0), end=time(8, 0), tz=ZoneInfo("Europe/Moscow")
        )
    text = _reply_text(update)
    assert "Europe/Moscow" in text
    assert "20:00" in text  # the UTC equivalent is echoed back
    assert "05:00" in text


async def test_quiethours_rejects_bad_times(session_factory) -> None:
    with session_factory() as session:
        players.set_timezone(session, 1, ZoneInfo("UTC"))
        session.commit()
    for args in (["25:00", "08:00"], ["23:00"], ["08:00", "08:00"], ["a", "b", "c"]):
        update = _update()
        await _run(qh_module.quiethours_command, update, _context(session_factory, args=args))
        update.message.reply_text.assert_awaited_once()
    with session_factory() as session:
        assert settings.get_quiet_hours(session) is None


async def test_quiethours_off_clears(session_factory) -> None:
    with session_factory() as session:
        settings.set_quiet_hours(
            session, QuietHours(start=time(23, 0), end=time(8, 0), tz=ZoneInfo("UTC"))
        )
        session.commit()
    update = _update()
    await _run(qh_module.quiethours_command, update, _context(session_factory, args=["off"]))
    with session_factory() as session:
        assert settings.get_quiet_hours(session) is None


async def test_timezone_command_sets_a_typed_zone(session_factory) -> None:
    update = _update()
    context = _context(session_factory, args=["Asia/Novosibirsk"])
    await _run(qh_module.timezone_command, update, context)
    with session_factory() as session:
        assert players.get_timezone(session, 1) == ZoneInfo("Asia/Novosibirsk")
    assert "Asia/Novosibirsk" in _reply_text(update)


async def test_timezone_command_rejects_unknown_zone(session_factory) -> None:
    for bad in ("Mars/Olympus", "../etc"):
        update = _update()
        await _run(qh_module.timezone_command, update, _context(session_factory, args=[bad]))
        assert bad in _reply_text(update)
    with session_factory() as session:
        assert players.get_timezone(session, 1) is None


async def test_timezone_command_rejects_non_admins(session_factory) -> None:
    update = _update()
    context = _context(session_factory, admin=False, args=["Europe/London"])
    await _run(qh_module.timezone_command, update, context)
    with session_factory() as session:
        assert players.get_timezone(session, 1) is None


async def test_timezone_command_without_args_shows_keyboard(session_factory) -> None:
    update = _update()
    await _run(qh_module.timezone_command, update, _context(session_factory))
    kwargs = update.message.reply_text.await_args.kwargs
    assert kwargs["reply_markup"] is not None


async def test_timezone_callback_sets_zone_for_admins(session_factory) -> None:
    update = _callback_update("set_timezone:Europe/London")
    await _run(qh_module.timezone_callback_handler, update, _context(session_factory))
    with session_factory() as session:
        assert players.get_timezone(session, 1) == ZoneInfo("Europe/London")
    update.callback_query.edit_message_text.assert_awaited_once()


async def test_timezone_callback_ignores_non_admins(session_factory) -> None:
    update = _callback_update("set_timezone:Europe/London")
    context = _context(session_factory, admin=False)
    await _run(qh_module.timezone_callback_handler, update, context)
    with session_factory() as session:
        assert players.get_timezone(session, 1) is None
