from typing import cast
from unittest.mock import AsyncMock, MagicMock

from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands import skip as skip_command_module
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.turn_state import TurnState


def _make_update(
    *,
    user_id: int = 1,
    chat_id: int = 555,
    thread_id: int | None = 7,
    args: list[str] | None = None,
) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.id = chat_id
    update.message.message_thread_id = thread_id
    update.effective_message = update.message
    update.message.reply_text = AsyncMock()
    return update


def _make_context(session_factory, *, args: list[str] | None = None) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    context.args = args or []
    context.job_queue.get_jobs_by_name.return_value = []
    return context


async def test_skip_command_ignores_outside_the_game_topic(session_factory) -> None:
    update = _make_update(thread_id=999)
    context = _make_context(session_factory)

    await skip_command_module.skip_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_not_awaited()


async def test_skip_command_rejects_while_a_game_is_running(session_factory) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.commit()
        session.add(Game(starter_id=1, original_file_id="f", status=GameStatus.ACTIVE))
        session.commit()

    update = _make_update(user_id=1)
    context = _make_context(session_factory)

    await skip_command_module.skip_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    assert "running" in update.message.reply_text.await_args.args[0].lower()


async def test_skip_command_rejects_when_it_is_not_their_turn(session_factory) -> None:
    with session_factory() as session:
        session.add_all([Player(telegram_user_id=1), Player(telegram_user_id=2)])
        session.add(TurnState(id=1, next_starter_id=2))
        session.commit()

    update = _make_update(user_id=1)
    context = _make_context(session_factory)

    await skip_command_module.skip_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    assert "turn" in update.message.reply_text.await_args.args[0].lower()


async def test_skip_command_bare_opens_the_turn(session_factory) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.add(TurnState(id=1, next_starter_id=1))
        session.commit()

    update = _make_update(user_id=1, args=[])
    context = _make_context(session_factory, args=[])

    await skip_command_module.skip_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        turn_state = session.get(TurnState, 1)
        assert turn_state is not None
        assert turn_state.next_starter_id is None
    update.message.reply_text.assert_awaited_once()


async def test_skip_command_bare_cancels_the_turn_timers(session_factory) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.add(TurnState(id=1, next_starter_id=1))
        session.commit()

    update = _make_update(user_id=1, args=[])
    context = _make_context(session_factory, args=[])

    await skip_command_module.skip_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    assert context.job_queue.get_jobs_by_name.call_count == 2


async def test_skip_command_with_username_hands_off_the_turn(session_factory) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.add(Player(telegram_user_id=2, username="friend"))
        session.commit()

    update = _make_update(user_id=1, args=["@friend"])
    context = _make_context(session_factory, args=["@friend"])

    await skip_command_module.skip_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        turn_state = session.get(TurnState, 1)
        assert turn_state is not None
        assert turn_state.next_starter_id == 2
    update.message.reply_text.assert_awaited_once()


async def test_skip_command_with_username_schedules_the_turn_timers(session_factory) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.add(Player(telegram_user_id=2, username="friend"))
        session.commit()

    update = _make_update(user_id=1, args=["@friend"])
    context = _make_context(session_factory, args=["@friend"])

    await skip_command_module.skip_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    names = [call.kwargs["name"] for call in context.job_queue.run_once.call_args_list]
    assert skip_command_module.timeout_module.TURN_REMINDER_JOB_NAME in names
    assert skip_command_module.timeout_module.TURN_EXPIRY_JOB_NAME in names


async def test_skip_command_rejects_an_unknown_username(session_factory) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.commit()

    update = _make_update(user_id=1, args=["@stranger"])
    context = _make_context(session_factory, args=["@stranger"])

    await skip_command_module.skip_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    assert "stranger" in update.message.reply_text.await_args.args[0].lower()
    with session_factory() as session:
        assert session.get(TurnState, 1) is None
