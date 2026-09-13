from typing import cast
from unittest.mock import AsyncMock, MagicMock

from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start import newgame
from nani_pix_bot.commands.dm_start.keyboards import (
    ANILIST_METHOD_CALLBACK_DATA,
    SHIKIMORI_METHOD_CALLBACK_DATA,
)
from nani_pix_bot.jobs import timers as timeout_module
from nani_pix_bot.models.bot_settings import BotSettings
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.turn_state import TurnState


def _make_update(
    *, user_id: int = 1, username: str = "player", full_name: str = "Starter Name"
) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.username = username
    update.effective_user.full_name = full_name
    update.effective_chat.type = "private"
    update.message.reply_text = AsyncMock()
    return update


def _make_context(session_factory, **extra_bot_data) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
        **extra_bot_data,
    }
    context.bot.get_chat_member = AsyncMock(return_value=MagicMock(status=ChatMemberStatus.MEMBER))
    context.bot.send_message = AsyncMock()
    context.job_queue.get_jobs_by_name.return_value = []
    return context


def _set_language(session_factory, language: str) -> None:
    with session_factory() as session:
        session.add(BotSettings(id=1, language=language))
        session.commit()


async def test_newgame_command_creates_a_setup_game_with_no_image_yet(session_factory) -> None:
    update = _make_update(user_id=1)
    context = _make_context(session_factory)

    await newgame.newgame_command(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    with session_factory() as session:
        games = session.query(Game).all()
        assert len(games) == 1
        assert games[0].status == GameStatus.SETUP
        assert games[0].original_image is None
        assert games[0].starter_id == 1
    update.message.reply_text.assert_awaited_once()


async def test_newgame_command_notifies_the_group_that_setup_started(session_factory) -> None:
    update = _make_update(user_id=1, full_name="Starter Name")
    context = _make_context(session_factory)

    await newgame.newgame_command(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_message.assert_awaited_once()
    _, kwargs = context.bot.send_message.await_args
    assert kwargs["chat_id"] == 555
    assert "Starter Name" in kwargs["text"]


async def test_newgame_command_shows_the_method_selection_keyboard(session_factory) -> None:
    update = _make_update(user_id=1)
    context = _make_context(session_factory)

    await newgame.newgame_command(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    _, kwargs = update.message.reply_text.await_args
    callbacks = [
        button.callback_data for row in kwargs["reply_markup"].inline_keyboard for button in row
    ]
    assert callbacks[:2] == [ANILIST_METHOD_CALLBACK_DATA, SHIKIMORI_METHOD_CALLBACK_DATA]


async def test_newgame_command_prefers_shikimori_first_when_language_is_ru(session_factory) -> None:
    _set_language(session_factory, "RU")
    update = _make_update(user_id=1)
    context = _make_context(session_factory)

    await newgame.newgame_command(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    _, kwargs = update.message.reply_text.await_args
    callbacks = [
        button.callback_data for row in kwargs["reply_markup"].inline_keyboard for button in row
    ]
    assert callbacks[:2] == [SHIKIMORI_METHOD_CALLBACK_DATA, ANILIST_METHOD_CALLBACK_DATA]


async def test_newgame_command_rejects_when_it_is_not_their_turn(session_factory) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=2))
        session.add(TurnState(id=1, next_starter_id=2))
        session.commit()

    update = _make_update(user_id=1)
    context = _make_context(session_factory)

    await newgame.newgame_command(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    with session_factory() as session:
        assert session.query(Game).count() == 0
    update.message.reply_text.assert_awaited_once()
    reply_text = update.message.reply_text.await_args.args[0]
    assert "turn" in reply_text.lower()


async def test_newgame_command_rejects_when_games_are_disabled(session_factory) -> None:
    with session_factory() as session:
        session.add(BotSettings(id=1, games_enabled=False))
        session.commit()

    update = _make_update(user_id=1)
    context = _make_context(session_factory)

    await newgame.newgame_command(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    with session_factory() as session:
        assert session.query(Game).count() == 0
    update.message.reply_text.assert_awaited_once()
    reply_text = update.message.reply_text.await_args.args[0]
    assert "disabled" in reply_text.lower() or "paused" in reply_text.lower()


async def test_newgame_command_rejects_non_group_members(session_factory) -> None:
    update = _make_update(user_id=1)
    context = _make_context(session_factory)
    context.bot.get_chat_member = AsyncMock(return_value=MagicMock(status=ChatMemberStatus.LEFT))

    await newgame.newgame_command(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    with session_factory() as session:
        assert session.query(Game).count() == 0
    update.message.reply_text.assert_awaited_once()
    reply_text = update.message.reply_text.await_args.args[0]
    assert "member" in reply_text.lower()


async def test_newgame_command_ignores_group_chat_messages(session_factory) -> None:
    update = _make_update(user_id=1)
    update.effective_chat.type = "supergroup"
    context = _make_context(session_factory)

    await newgame.newgame_command(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    with session_factory() as session:
        assert session.query(Game).count() == 0
    update.message.reply_text.assert_not_awaited()


async def test_newgame_command_schedules_the_setup_abandon_timer(session_factory) -> None:
    update = _make_update(user_id=1)
    context = _make_context(session_factory)

    await newgame.newgame_command(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    with session_factory() as session:
        game = session.query(Game).filter_by(starter_id=1).one()
    names = [call.kwargs["name"] for call in context.job_queue.run_once.call_args_list]
    assert timeout_module.setup_abandon_job_name(game.id) in names
