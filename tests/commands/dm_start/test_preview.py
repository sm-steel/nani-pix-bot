from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start import preview, search
from nani_pix_bot.commands.dm_start.keyboards import (
    ANILIST_METHOD_CALLBACK_DATA,
    PREVIEW_ADD_SYNONYM_CALLBACK_DATA,
    PREVIEW_CHANGE_IMAGE_CALLBACK_DATA,
    PREVIEW_CONFIRM_CALLBACK_DATA,
    PREVIEW_RESEARCH_CALLBACK_DATA,
)
from nani_pix_bot.models.enums import GameStatus, SetupStep
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services.search.anilist import AniListResult

_FRIEREN = AniListResult(
    anilist_id=99,
    title_romaji="Sousou no Frieren",
    title_english="Frieren: Beyond Journey's End",
    title_native="葬送のフリーレン",
    synonyms=["Frieren"],
    year=2023,
)


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


def _make_callback_context(session_factory, **extra_bot_data) -> MagicMock:
    context = _make_context(
        session_factory, game_topic_id=7, search_client=MagicMock(), **extra_bot_data
    )
    context.bot.get_file = AsyncMock()
    context.bot.get_file.return_value.download_as_bytearray = AsyncMock(
        return_value=bytearray(b"original-bytes")
    )
    context.bot.send_photo = AsyncMock()
    context.bot.send_media_group = AsyncMock()
    return context


def _make_text_update(
    *, user_id: int = 1, text: str = "frieren", full_name: str = "Starter Name"
) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.full_name = full_name
    update.effective_chat.type = "private"
    update.message.text = text
    update.message.reply_text = AsyncMock()
    return update


def _make_preview_callback_update(
    *, data: str, user_id: int = 1, full_name: str = "Starter Name"
) -> MagicMock:
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.from_user.id = user_id
    update.callback_query.from_user.full_name = full_name
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


def _staged_setup_game(session_factory, *, starter_id: int = 1) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=starter_id))
        session.commit()
        game = game_service.create_setup_game(
            session, starter_id=starter_id, original_file_id="file123"
        )
        game.source = "anilist"
        game_service.stage_result(game, _FRIEREN, source="anilist")
        game.setup_step = SetupStep.CONFIRMING
        session.commit()


async def test_preview_confirm_activates_and_posts_to_the_group(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(preview.pixelate_service, "pixelate", lambda data, stage: b"pixelated")
    _staged_setup_game(session_factory)
    update = _make_preview_callback_update(data=PREVIEW_CONFIRM_CALLBACK_DATA, user_id=1)
    context = _make_callback_context(session_factory)

    await preview.preview_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.bot.send_photo.assert_awaited_once()
    _, kwargs = context.bot.send_photo.await_args
    assert kwargs["chat_id"] == 555
    assert kwargs["message_thread_id"] == 7
    assert "Starter Name" in kwargs["caption"]

    with session_factory() as session:
        fetched = session.query(Game).filter_by(starter_id=1).one()
        assert fetched.status == GameStatus.ACTIVE

    context.job_queue.run_once.assert_called_once()
    context.job_queue.get_jobs_by_name.assert_any_call(
        preview.timeout_module.setup_abandon_job_name(fetched.id)
    )
    update.callback_query.edit_message_text.assert_awaited_once()


async def test_preview_change_image_awaits_a_new_photo(session_factory) -> None:
    _staged_setup_game(session_factory)
    update = _make_preview_callback_update(data=PREVIEW_CHANGE_IMAGE_CALLBACK_DATA, user_id=1)
    context = _make_context(session_factory)

    await preview.preview_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.callback_query.edit_message_text.assert_awaited_once()
    with session_factory() as session:
        fetched = session.query(Game).filter_by(starter_id=1).one()
        assert fetched.setup_step == SetupStep.AWAITING_PHOTO_CHANGE
        assert fetched.status == GameStatus.SETUP


async def test_preview_research_returns_to_the_method_keyboard(session_factory) -> None:
    _staged_setup_game(session_factory)
    update = _make_preview_callback_update(data=PREVIEW_RESEARCH_CALLBACK_DATA, user_id=1)
    context = _make_context(session_factory)

    await preview.preview_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    _, kwargs = update.callback_query.edit_message_text.await_args
    callbacks = [
        button.callback_data for row in kwargs["reply_markup"].inline_keyboard for button in row
    ]
    assert ANILIST_METHOD_CALLBACK_DATA in callbacks
    with session_factory() as session:
        fetched = session.query(Game).filter_by(starter_id=1).one()
        assert fetched.setup_step == SetupStep.PICKING_METHOD


async def test_preview_add_synonym_awaits_a_synonym_message(session_factory) -> None:
    _staged_setup_game(session_factory)
    update = _make_preview_callback_update(data=PREVIEW_ADD_SYNONYM_CALLBACK_DATA, user_id=1)
    context = _make_context(session_factory)

    await preview.preview_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.callback_query.edit_message_text.assert_awaited_once()
    with session_factory() as session:
        fetched = session.query(Game).filter_by(starter_id=1).one()
        assert fetched.setup_step == SetupStep.AWAITING_SYNONYM


async def test_search_text_handler_appends_a_synonym_and_reshows_the_preview(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(preview.pixelate_service, "pixelate", lambda data, stage: b"pixelated")
    _staged_setup_game(session_factory)
    with session_factory() as session:
        game = session.query(Game).filter_by(starter_id=1).one()
        game.setup_step = SetupStep.AWAITING_SYNONYM
        session.commit()

    update = _make_text_update(user_id=1, text="Frieren at the Funeral")
    context = _make_callback_context(session_factory)

    await search.search_text_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_media_group.assert_awaited_once()
    _, kwargs = context.bot.send_media_group.await_args
    assert kwargs["chat_id"] == 1
    with session_factory() as session:
        fetched = session.query(Game).filter_by(starter_id=1).one()
        assert fetched.synonyms == ["Frieren", "Frieren at the Funeral"]
        assert fetched.setup_step == SetupStep.CONFIRMING


async def test_search_text_handler_rejects_a_blank_extra_synonym(session_factory) -> None:
    _staged_setup_game(session_factory)
    with session_factory() as session:
        game = session.query(Game).filter_by(starter_id=1).one()
        game.setup_step = SetupStep.AWAITING_SYNONYM
        session.commit()

    update = _make_text_update(user_id=1, text="   ")
    context = _make_callback_context(session_factory)

    await search.search_text_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_photo.assert_not_awaited()
    update.message.reply_text.assert_awaited_once()
    with session_factory() as session:
        fetched = session.query(Game).filter_by(starter_id=1).one()
        assert fetched.setup_step == SetupStep.AWAITING_SYNONYM


async def test_search_text_handler_ignores_text_while_confirming(session_factory) -> None:
    _staged_setup_game(session_factory)
    update = _make_text_update(user_id=1, text="whatever")
    context = _make_callback_context(session_factory)

    await search.search_text_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    update.message.reply_text.assert_not_awaited()
    context.bot.send_photo.assert_not_awaited()


async def test_search_text_handler_ignores_text_while_awaiting_a_photo_change(
    session_factory,
) -> None:
    _staged_setup_game(session_factory)
    with session_factory() as session:
        game = session.query(Game).filter_by(starter_id=1).one()
        game.setup_step = SetupStep.AWAITING_PHOTO_CHANGE
        session.commit()

    update = _make_text_update(user_id=1, text="whatever")
    context = _make_callback_context(session_factory)

    await search.search_text_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    update.message.reply_text.assert_not_awaited()
    context.bot.send_photo.assert_not_awaited()
