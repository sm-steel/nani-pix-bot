from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start import preview, search
from nani_pix_bot.models.enums import GameStatus, SetupStep
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import game as game_service


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


def _create_setup_game(
    session_factory, *, starter_id: int = 1, file_id: str = "file123", source: str = "anilist"
) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=starter_id))
        session.commit()
        game = game_service.create_setup_game(
            session, starter_id=starter_id, original_file_id=file_id
        )
        game.source = source
        session.commit()


async def test_manual_entry_first_message_sets_the_title_and_asks_for_synonyms(
    session_factory,
) -> None:
    _create_setup_game(session_factory, starter_id=1, source="manual")
    update = _make_text_update(user_id=1, text="Sousou no Frieren")
    context = _make_callback_context(session_factory)

    await search.search_text_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    update.message.reply_text.assert_awaited_once()
    reply_text = update.message.reply_text.await_args.args[0]
    assert "synonym" in reply_text.lower()
    context.bot.send_photo.assert_not_awaited()
    with session_factory() as session:
        fetched = session.query(Game).filter_by(starter_id=1).one()
        assert fetched.title_english == "Sousou no Frieren"
        assert fetched.status == GameStatus.SETUP


async def test_manual_entry_rejects_a_blank_synonym_message_with_a_reprompt(
    session_factory,
) -> None:
    _create_setup_game(session_factory, starter_id=1, source="manual")
    with session_factory() as session:
        game = session.query(Game).filter_by(starter_id=1).one()
        game.title_english = "Sousou no Frieren"
        session.commit()

    update = _make_text_update(user_id=1, text="   ")
    context = _make_callback_context(session_factory)

    await search.search_text_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    update.message.reply_text.assert_awaited_once()
    reply_text = update.message.reply_text.await_args.args[0]
    assert "synonym" in reply_text.lower()
    context.bot.send_photo.assert_not_awaited()
    with session_factory() as session:
        fetched = session.query(Game).filter_by(starter_id=1).one()
        assert fetched.status == GameStatus.SETUP


async def test_manual_entry_second_message_stages_and_shows_a_preview(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(preview.pixelate_service, "pixelate", lambda data, stage: b"pixelated")
    _create_setup_game(session_factory, starter_id=1, source="manual")
    with session_factory() as session:
        game = session.query(Game).filter_by(starter_id=1).one()
        game.title_english = "Sousou no Frieren"
        session.commit()

    update = _make_text_update(
        user_id=1, text="Frieren, Frieren at the Funeral", full_name="Starter Name"
    )
    context = _make_callback_context(session_factory)

    await search.search_text_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_media_group.assert_awaited_once()
    _, kwargs = context.bot.send_media_group.await_args
    assert kwargs["chat_id"] == 1
    assert all(item.media.input_file_content == b"pixelated" for item in kwargs["media"])
    assert "Sousou no Frieren" in kwargs["media"][0].caption

    with session_factory() as session:
        fetched = session.query(Game).filter_by(starter_id=1).one()
        assert fetched.status == GameStatus.SETUP
        assert fetched.setup_step == SetupStep.CONFIRMING
        assert fetched.source == "manual"
        assert fetched.title_english == "Sousou no Frieren"
        assert fetched.synonyms == ["Frieren", "Frieren at the Funeral"]

    update.message.reply_text.assert_awaited_once()
