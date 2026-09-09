from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands import dm_start
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.turn_state import TurnState
from nani_pix_bot.services import anilist
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services.anilist import AniListResult

_FRIEREN = AniListResult(
    anilist_id=99,
    title_romaji="Sousou no Frieren",
    title_english="Frieren: Beyond Journey's End",
    title_native="葬送のフリーレン",
    synonyms=["Frieren"],
    year=2023,
)


def _make_update(
    *, user_id: int = 1, username: str = "player", photo_file_id: str | None = None
) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.username = username
    update.effective_chat.type = "private"
    update.message.reply_text = AsyncMock()
    update.message.photo = [MagicMock(file_id=photo_file_id)] if photo_file_id else []
    return update


def _make_context(session_factory, **extra_bot_data) -> MagicMock:
    context = MagicMock()
    context.bot_data = {"session_factory": session_factory, **extra_bot_data}
    context.user_data = {}
    return context


def _make_text_update(*, user_id: int = 1, text: str = "frieren") -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.type = "private"
    update.message.text = text
    update.message.reply_text = AsyncMock()
    return update


async def test_photo_handler_creates_a_setup_game_when_turn_is_open(session_factory) -> None:
    update = _make_update(user_id=1, photo_file_id="file123")
    context = _make_context(session_factory)

    await dm_start.photo_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    with session_factory() as session:
        games = session.query(Game).all()
        assert len(games) == 1
        assert games[0].status == GameStatus.SETUP
        assert games[0].original_file_id == "file123"
        assert games[0].starter_id == 1
        assert context.user_data[dm_start.PENDING_GAME_ID_KEY] == games[0].id
    update.message.reply_text.assert_awaited_once()


async def test_photo_handler_rejects_when_it_is_not_their_turn(session_factory) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=2))
        session.add(TurnState(id=1, next_starter_id=2))
        session.commit()

    update = _make_update(user_id=1, photo_file_id="file123")
    context = _make_context(session_factory)

    await dm_start.photo_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    with session_factory() as session:
        assert session.query(Game).count() == 0
    update.message.reply_text.assert_awaited_once()
    reply_text = update.message.reply_text.await_args.args[0]
    assert "turn" in reply_text.lower()


async def test_photo_handler_ignores_non_photo_messages(session_factory) -> None:
    update = _make_update(user_id=1, photo_file_id=None)
    context = _make_context(session_factory)

    await dm_start.photo_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    with session_factory() as session:
        assert session.query(Game).count() == 0
    update.message.reply_text.assert_not_awaited()


async def test_search_text_handler_ignores_when_no_game_is_pending(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    search_mock = AsyncMock()
    monkeypatch.setattr(anilist, "search", search_mock)
    update = _make_text_update()
    context = _make_context(session_factory, anilist_client=MagicMock())

    await dm_start.search_text_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    search_mock.assert_not_awaited()
    update.message.reply_text.assert_not_awaited()


async def test_search_text_handler_replies_when_no_results(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(anilist, "search", AsyncMock(return_value=[]))
    update = _make_text_update()
    context = _make_context(session_factory, anilist_client=MagicMock())
    context.user_data[dm_start.PENDING_GAME_ID_KEY] = 1

    await dm_start.search_text_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    assert "no" in update.message.reply_text.await_args.args[0].lower()


async def test_search_text_handler_shows_keyboard_on_results(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(anilist, "search", AsyncMock(return_value=[_FRIEREN]))
    update = _make_text_update()
    context = _make_context(session_factory, anilist_client=MagicMock())
    context.user_data[dm_start.PENDING_GAME_ID_KEY] = 1

    await dm_start.search_text_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    _, kwargs = update.message.reply_text.await_args
    assert kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "anilist_pick:99"
    assert context.user_data[dm_start.SEARCH_RESULTS_KEY] == {99: _FRIEREN}


def _make_callback_context(session_factory, **extra_bot_data) -> MagicMock:
    context = _make_context(session_factory, group_chat_id=555, game_topic_id=7, **extra_bot_data)
    context.bot.get_file = AsyncMock()
    context.bot.get_file.return_value.download_as_bytearray = AsyncMock(
        return_value=bytearray(b"original-bytes")
    )
    context.bot.send_photo = AsyncMock()
    return context


def _make_callback_update(*, data: str) -> MagicMock:
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


async def test_pick_callback_handler_retry_does_not_touch_the_database(session_factory) -> None:
    update = _make_callback_update(data=dm_start.RETRY_CALLBACK_DATA)
    context = _make_callback_context(session_factory)

    await dm_start.pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.callback_query.answer.assert_awaited_once()
    update.callback_query.edit_message_text.assert_awaited_once()
    context.bot.send_photo.assert_not_awaited()


async def test_pick_callback_handler_activates_the_game_on_a_valid_pick(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dm_start.pixelate_service, "pixelate", lambda data, stage: b"pixelated")
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.commit()
        setup_game = game_service.create_setup_game(
            session, starter_id=1, original_file_id="file123"
        )
        session.commit()
        game_id = setup_game.id

    update = _make_callback_update(data="anilist_pick:99")
    context = _make_callback_context(session_factory)
    context.user_data[dm_start.PENDING_GAME_ID_KEY] = game_id
    context.user_data[dm_start.SEARCH_RESULTS_KEY] = {99: _FRIEREN}

    await dm_start.pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.bot.get_file.assert_awaited_once_with("file123")
    context.bot.send_photo.assert_awaited_once()
    _, kwargs = context.bot.send_photo.await_args
    assert kwargs == {"chat_id": 555, "message_thread_id": 7, "photo": b"pixelated"}

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.status == GameStatus.ACTIVE
        assert fetched.anilist_id == 99

    assert dm_start.PENDING_GAME_ID_KEY not in context.user_data
    assert dm_start.SEARCH_RESULTS_KEY not in context.user_data
    update.callback_query.edit_message_text.assert_awaited_once()


async def test_pick_callback_handler_schedules_the_timeout_job(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dm_start.pixelate_service, "pixelate", lambda data, stage: b"pixelated")
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.commit()
        setup_game = game_service.create_setup_game(
            session, starter_id=1, original_file_id="file123"
        )
        session.commit()
        game_id = setup_game.id

    update = _make_callback_update(data="anilist_pick:99")
    context = _make_callback_context(session_factory)
    context.user_data[dm_start.PENDING_GAME_ID_KEY] = game_id
    context.user_data[dm_start.SEARCH_RESULTS_KEY] = {99: _FRIEREN}

    await dm_start.pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.job_queue.run_once.assert_called_once()
    _, kwargs = context.job_queue.run_once.call_args
    assert kwargs["name"] == dm_start.game_service.timeout_job_name(game_id)
