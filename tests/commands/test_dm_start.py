from typing import cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes

from nani_pix_bot.commands import dm_start
from nani_pix_bot.commands.helpers.keyboards import (
    ANILIST_METHOD_CALLBACK_DATA,
    MANUAL_METHOD_CALLBACK_DATA,
    SHIKIMORI_METHOD_CALLBACK_DATA,
)
from nani_pix_bot.models.bot_settings import BotSettings
from nani_pix_bot.models.enums import GameStatus, SetupStep
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.turn_state import TurnState
from nani_pix_bot.services import anilist, shikimori
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services.anilist import AniListResult
from nani_pix_bot.services.shikimori import ShikimoriResult

_FRIEREN = AniListResult(
    anilist_id=99,
    title_romaji="Sousou no Frieren",
    title_english="Frieren: Beyond Journey's End",
    title_native="葬送のフリーレン",
    synonyms=["Frieren"],
    year=2023,
)

_FRIEREN_SHIKIMORI = ShikimoriResult(
    shikimori_id=52991,
    title_romaji="Sousou no Frieren",
    title_english="Frieren: Beyond Journey's End",
    title_russian="Провожающая в последний путь Фрирен",
    synonyms=["Frieren at the Funeral"],
)


def _make_update(
    *,
    user_id: int = 1,
    username: str = "player",
    full_name: str = "Starter Name",
    photo_file_id: str | None = None,
) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.username = username
    update.effective_user.full_name = full_name
    update.effective_chat.type = "private"
    update.message.reply_text = AsyncMock()
    update.message.photo = [MagicMock(file_id=photo_file_id)] if photo_file_id else []
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


def _set_language(session_factory, language: str) -> None:
    with session_factory() as session:
        session.add(BotSettings(id=1, language=language))
        session.commit()


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
    update.message.reply_text.assert_awaited_once()


async def test_photo_handler_notifies_the_group_that_setup_started(session_factory) -> None:
    update = _make_update(user_id=1, photo_file_id="file123", full_name="Starter Name")
    context = _make_context(session_factory)

    await dm_start.photo_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_message.assert_awaited_once()
    _, kwargs = context.bot.send_message.await_args
    assert kwargs["chat_id"] == 555
    assert "Starter Name" in kwargs["text"]


async def test_photo_handler_schedules_the_setup_abandon_timer(session_factory) -> None:
    update = _make_update(user_id=1, photo_file_id="file123")
    context = _make_context(session_factory)

    await dm_start.photo_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    with session_factory() as session:
        game = session.query(Game).filter_by(starter_id=1).one()
    names = [call.kwargs["name"] for call in context.job_queue.run_once.call_args_list]
    assert dm_start.game_service.setup_abandon_job_name(game.id) in names


async def test_photo_handler_cancels_turn_timers_when_the_designated_starter_begins(
    session_factory,
) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.add(TurnState(id=1, next_starter_id=1))
        session.commit()

    update = _make_update(user_id=1, photo_file_id="file123")
    context = _make_context(session_factory)

    await dm_start.photo_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    assert context.job_queue.get_jobs_by_name.call_count >= 2


async def test_photo_handler_shows_the_method_selection_keyboard(session_factory) -> None:
    update = _make_update(user_id=1, photo_file_id="file123")
    context = _make_context(session_factory)

    await dm_start.photo_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    _, kwargs = update.message.reply_text.await_args
    callbacks = [
        button.callback_data for row in kwargs["reply_markup"].inline_keyboard for button in row
    ]
    assert callbacks[:2] == [ANILIST_METHOD_CALLBACK_DATA, SHIKIMORI_METHOD_CALLBACK_DATA]


async def test_photo_handler_prefers_shikimori_first_when_language_is_ru(session_factory) -> None:
    _set_language(session_factory, "RU")
    update = _make_update(user_id=1, photo_file_id="file123")
    context = _make_context(session_factory)

    await dm_start.photo_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    _, kwargs = update.message.reply_text.await_args
    callbacks = [
        button.callback_data for row in kwargs["reply_markup"].inline_keyboard for button in row
    ]
    assert callbacks[:2] == [SHIKIMORI_METHOD_CALLBACK_DATA, ANILIST_METHOD_CALLBACK_DATA]


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


async def test_photo_handler_rejects_non_group_members(session_factory) -> None:
    update = _make_update(user_id=1, photo_file_id="file123")
    context = _make_context(session_factory)
    context.bot.get_chat_member = AsyncMock(return_value=MagicMock(status=ChatMemberStatus.LEFT))

    await dm_start.photo_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    with session_factory() as session:
        assert session.query(Game).count() == 0
    update.message.reply_text.assert_awaited_once()
    reply_text = update.message.reply_text.await_args.args[0]
    assert "member" in reply_text.lower()


def _make_method_callback_update(*, data: str, user_id: int = 1) -> MagicMock:
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.from_user.id = user_id
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


async def test_method_pick_callback_handler_stores_the_source_and_prompts_for_search(
    session_factory,
) -> None:
    _create_setup_game(session_factory, starter_id=1)
    update = _make_method_callback_update(data=SHIKIMORI_METHOD_CALLBACK_DATA, user_id=1)
    context = _make_context(session_factory)

    await dm_start.method_pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.callback_query.answer.assert_awaited_once()
    update.callback_query.edit_message_text.assert_awaited_once()
    with session_factory() as session:
        fetched = session.query(Game).filter_by(starter_id=1).one()
        assert fetched.source == "shikimori"
        assert fetched.status == GameStatus.SETUP


async def test_method_pick_callback_handler_prompts_for_a_title_when_manual_is_picked(
    session_factory,
) -> None:
    _create_setup_game(session_factory, starter_id=1)
    update = _make_method_callback_update(data=MANUAL_METHOD_CALLBACK_DATA, user_id=1)
    context = _make_context(session_factory)

    await dm_start.method_pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "title" in text.lower()
    with session_factory() as session:
        fetched = session.query(Game).filter_by(starter_id=1).one()
        assert fetched.source == "manual"


async def test_search_text_handler_ignores_when_no_game_is_pending(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    search_mock = AsyncMock()
    monkeypatch.setattr(anilist, "search", search_mock)
    update = _make_text_update()
    context = _make_context(session_factory, search_client=MagicMock())

    await dm_start.search_text_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    search_mock.assert_not_awaited()
    update.message.reply_text.assert_not_awaited()


async def test_search_text_handler_sends_a_searching_message_immediately(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The starter should see feedback right away, before the AniList/
    # Shikimori round-trip (which can take a few seconds, more over
    # Shikimori's proxy hop) resolves — see the search-feedback fix.
    _create_setup_game(session_factory, starter_id=1)
    monkeypatch.setattr(anilist, "search", AsyncMock(return_value=[]))
    update = _make_text_update(user_id=1)
    context = _make_context(session_factory, search_client=MagicMock())

    await dm_start.search_text_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    assert "search" in update.message.reply_text.await_args.args[0].lower()


async def test_search_text_handler_finds_the_pending_game_from_the_db(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Simulates a bot restart between the photo and the search: nothing in
    # user_data, only the DB row — see issue #11.
    _create_setup_game(session_factory, starter_id=1)
    monkeypatch.setattr(anilist, "search", AsyncMock(return_value=[]))
    update = _make_text_update(user_id=1)
    context = _make_context(session_factory, search_client=MagicMock())

    await dm_start.search_text_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    status_message = update.message.reply_text.return_value
    status_message.edit_text.assert_awaited_once()
    assert "no" in status_message.edit_text.await_args.args[0].lower()


async def test_search_text_handler_shows_keyboard_on_anilist_results(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create_setup_game(session_factory, starter_id=1, source="anilist")
    monkeypatch.setattr(anilist, "search", AsyncMock(return_value=[_FRIEREN]))
    update = _make_text_update(user_id=1)
    context = _make_context(session_factory, search_client=MagicMock())

    await dm_start.search_text_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    status_message = update.message.reply_text.return_value
    status_message.edit_text.assert_awaited_once()
    _, kwargs = status_message.edit_text.await_args
    assert kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "anilist_pick:99"


async def test_search_text_handler_uses_shikimori_when_that_is_the_chosen_source(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create_setup_game(session_factory, starter_id=1, source="shikimori")
    search_mock = AsyncMock(return_value=[_FRIEREN_SHIKIMORI])
    monkeypatch.setattr(shikimori, "search", search_mock)
    update = _make_text_update(user_id=1)
    context = _make_context(session_factory, search_client=MagicMock())

    await dm_start.search_text_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    search_mock.assert_awaited_once_with(context.bot_data["search_client"], "frieren")
    status_message = update.message.reply_text.return_value
    _, kwargs = status_message.edit_text.await_args
    assert kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "shikimori_pick:52991"


async def test_search_text_handler_reshows_method_keyboard_when_the_service_errors(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create_setup_game(session_factory, starter_id=1, source="anilist")
    monkeypatch.setattr(anilist, "search", AsyncMock(side_effect=httpx.ConnectError("boom")))
    update = _make_text_update(user_id=1)
    context = _make_context(session_factory, search_client=MagicMock())

    await dm_start.search_text_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    status_message = update.message.reply_text.return_value
    status_message.edit_text.assert_awaited_once()
    _, kwargs = status_message.edit_text.await_args
    callbacks = [
        button.callback_data for row in kwargs["reply_markup"].inline_keyboard for button in row
    ]
    assert ANILIST_METHOD_CALLBACK_DATA in callbacks
    assert SHIKIMORI_METHOD_CALLBACK_DATA in callbacks
    # The game stays SETUP, un-staged — the starter can just pick again.
    with session_factory() as session:
        fetched = session.query(Game).filter_by(starter_id=1).one()
        assert fetched.status == GameStatus.SETUP


def _make_callback_context(session_factory, **extra_bot_data) -> MagicMock:
    context = _make_context(
        session_factory, game_topic_id=7, search_client=MagicMock(), **extra_bot_data
    )
    context.bot.get_file = AsyncMock()
    context.bot.get_file.return_value.download_as_bytearray = AsyncMock(
        return_value=bytearray(b"original-bytes")
    )
    context.bot.send_photo = AsyncMock()
    return context


def _make_callback_update(
    *, data: str, user_id: int = 1, full_name: str = "Starter Name"
) -> MagicMock:
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.from_user.id = user_id
    update.callback_query.from_user.full_name = full_name
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


async def test_pick_callback_handler_shows_a_preview_on_a_valid_anilist_pick(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dm_start.pixelate_service, "pixelate", lambda data, stage: b"pixelated")
    monkeypatch.setattr(dm_start.anilist, "get_by_id", AsyncMock(return_value=_FRIEREN))
    _create_setup_game(session_factory, starter_id=1, source="anilist")

    update = _make_callback_update(data="anilist_pick:99", user_id=1)
    context = _make_callback_context(session_factory)

    await dm_start.pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.bot.get_file.assert_awaited_once_with("file123")
    context.bot.send_photo.assert_awaited_once()
    _, kwargs = context.bot.send_photo.await_args
    # The preview goes to the starter's own DM, not the group topic —
    # nothing is posted to the group until they confirm.
    assert kwargs["chat_id"] == 1
    assert "message_thread_id" not in kwargs
    assert kwargs["photo"] == b"pixelated"
    assert "Frieren: Beyond Journey's End" in kwargs["caption"]

    with session_factory() as session:
        fetched = session.query(Game).filter_by(starter_id=1).one()
        assert fetched.status == GameStatus.SETUP
        assert fetched.setup_step == SetupStep.CONFIRMING
        assert fetched.anilist_id == 99
        assert fetched.source == "anilist"

    context.job_queue.run_once.assert_not_called()
    update.callback_query.edit_message_text.assert_awaited_once()


async def test_pick_callback_handler_shows_a_preview_on_a_valid_shikimori_pick(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dm_start.pixelate_service, "pixelate", lambda data, stage: b"pixelated")
    get_by_id_mock = AsyncMock(return_value=_FRIEREN_SHIKIMORI)
    monkeypatch.setattr(dm_start.shikimori, "get_by_id", get_by_id_mock)
    _create_setup_game(session_factory, starter_id=1, source="shikimori")

    update = _make_callback_update(data="shikimori_pick:52991", user_id=1)
    context = _make_callback_context(session_factory)

    await dm_start.pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    get_by_id_mock.assert_awaited_once_with(context.bot_data["search_client"], 52991)
    context.bot.send_photo.assert_awaited_once()

    with session_factory() as session:
        fetched = session.query(Game).filter_by(starter_id=1).one()
        assert fetched.status == GameStatus.SETUP
        assert fetched.setup_step == SetupStep.CONFIRMING
        assert fetched.source == "shikimori"
        assert fetched.title_russian == "Провожающая в последний путь Фрирен"


async def test_pick_callback_handler_reshows_method_keyboard_when_get_by_id_errors(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        dm_start.anilist, "get_by_id", AsyncMock(side_effect=httpx.ConnectError("boom"))
    )
    _create_setup_game(session_factory, starter_id=1, source="anilist")

    update = _make_callback_update(data="anilist_pick:99", user_id=1)
    context = _make_callback_context(session_factory)

    await dm_start.pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.bot.send_photo.assert_not_awaited()
    update.callback_query.edit_message_text.assert_awaited_once()
    _, kwargs = update.callback_query.edit_message_text.await_args
    callbacks = [
        button.callback_data for row in kwargs["reply_markup"].inline_keyboard for button in row
    ]
    assert ANILIST_METHOD_CALLBACK_DATA in callbacks
    assert SHIKIMORI_METHOD_CALLBACK_DATA in callbacks
    with session_factory() as session:
        fetched = session.query(Game).filter_by(starter_id=1).one()
        assert fetched.status == GameStatus.SETUP


async def test_pick_callback_handler_survives_a_restart_between_search_and_pick(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No cached search results anywhere — get_by_id re-fetches from AniList
    # using only the anilist_id embedded in the button's callback_data,
    # and the pending game is looked up fresh from the DB. See issue #11.
    monkeypatch.setattr(dm_start.pixelate_service, "pixelate", lambda data, stage: b"pixelated")
    get_by_id_mock = AsyncMock(return_value=_FRIEREN)
    monkeypatch.setattr(dm_start.anilist, "get_by_id", get_by_id_mock)
    _create_setup_game(session_factory, starter_id=1)

    update = _make_callback_update(data="anilist_pick:99", user_id=1)
    context = _make_callback_context(session_factory)

    await dm_start.pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    get_by_id_mock.assert_awaited_once_with(context.bot_data["search_client"], 99)
    context.bot.send_photo.assert_awaited_once()


async def test_pick_callback_handler_preview_lists_the_synonyms(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dm_start.pixelate_service, "pixelate", lambda data, stage: b"pixelated")
    monkeypatch.setattr(dm_start.anilist, "get_by_id", AsyncMock(return_value=_FRIEREN))
    _create_setup_game(session_factory, starter_id=1)

    update = _make_callback_update(data="anilist_pick:99", user_id=1)
    context = _make_callback_context(session_factory)

    await dm_start.pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    _, kwargs = context.bot.send_photo.await_args
    assert "Frieren" in kwargs["caption"]
    callbacks = [
        button.callback_data for row in kwargs["reply_markup"].inline_keyboard for button in row
    ]
    assert dm_start.PREVIEW_CONFIRM_CALLBACK_DATA in callbacks


async def test_manual_entry_first_message_sets_the_title_and_asks_for_synonyms(
    session_factory,
) -> None:
    _create_setup_game(session_factory, starter_id=1, source="manual")
    update = _make_text_update(user_id=1, text="Sousou no Frieren")
    context = _make_callback_context(session_factory)

    await dm_start.search_text_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

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

    await dm_start.search_text_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

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
    monkeypatch.setattr(dm_start.pixelate_service, "pixelate", lambda data, stage: b"pixelated")
    _create_setup_game(session_factory, starter_id=1, source="manual")
    with session_factory() as session:
        game = session.query(Game).filter_by(starter_id=1).one()
        game.title_english = "Sousou no Frieren"
        session.commit()

    update = _make_text_update(
        user_id=1, text="Frieren, Frieren at the Funeral", full_name="Starter Name"
    )
    context = _make_callback_context(session_factory)

    await dm_start.search_text_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.bot.send_photo.assert_awaited_once()
    _, kwargs = context.bot.send_photo.await_args
    assert kwargs["chat_id"] == 1
    assert kwargs["photo"] == b"pixelated"
    assert "Sousou no Frieren" in kwargs["caption"]

    with session_factory() as session:
        fetched = session.query(Game).filter_by(starter_id=1).one()
        assert fetched.status == GameStatus.SETUP
        assert fetched.setup_step == SetupStep.CONFIRMING
        assert fetched.source == "manual"
        assert fetched.title_english == "Sousou no Frieren"
        assert fetched.synonyms == ["Frieren", "Frieren at the Funeral"]

    update.message.reply_text.assert_awaited_once()


def _make_preview_callback_update(
    *, data: str, user_id: int = 1, full_name: str = "Starter Name"
) -> MagicMock:
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.from_user.id = user_id
    update.callback_query.from_user.full_name = full_name
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_caption = AsyncMock()
    return update


def _staged_setup_game(session_factory, *, starter_id: int = 1) -> None:
    _create_setup_game(session_factory, starter_id=starter_id, source="anilist")
    with session_factory() as session:
        game = session.query(Game).filter_by(starter_id=starter_id).one()
        game_service.stage_result(game, _FRIEREN, source="anilist")
        game.setup_step = SetupStep.CONFIRMING
        session.commit()


async def test_preview_confirm_activates_and_posts_to_the_group(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dm_start.pixelate_service, "pixelate", lambda data, stage: b"pixelated")
    _staged_setup_game(session_factory)
    update = _make_preview_callback_update(data=dm_start.PREVIEW_CONFIRM_CALLBACK_DATA, user_id=1)
    context = _make_callback_context(session_factory)

    await dm_start.preview_callback_handler(
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
        dm_start.game_service.setup_abandon_job_name(fetched.id)
    )
    update.callback_query.edit_message_caption.assert_awaited_once()


async def test_preview_change_image_awaits_a_new_photo(session_factory) -> None:
    _staged_setup_game(session_factory)
    update = _make_preview_callback_update(
        data=dm_start.PREVIEW_CHANGE_IMAGE_CALLBACK_DATA, user_id=1
    )
    context = _make_context(session_factory)

    await dm_start.preview_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.callback_query.edit_message_caption.assert_awaited_once()
    with session_factory() as session:
        fetched = session.query(Game).filter_by(starter_id=1).one()
        assert fetched.setup_step == SetupStep.AWAITING_PHOTO_CHANGE
        assert fetched.status == GameStatus.SETUP


async def test_preview_research_returns_to_the_method_keyboard(session_factory) -> None:
    _staged_setup_game(session_factory)
    update = _make_preview_callback_update(data=dm_start.PREVIEW_RESEARCH_CALLBACK_DATA, user_id=1)
    context = _make_context(session_factory)

    await dm_start.preview_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    _, kwargs = update.callback_query.edit_message_caption.await_args
    callbacks = [
        button.callback_data for row in kwargs["reply_markup"].inline_keyboard for button in row
    ]
    assert ANILIST_METHOD_CALLBACK_DATA in callbacks
    with session_factory() as session:
        fetched = session.query(Game).filter_by(starter_id=1).one()
        assert fetched.setup_step == SetupStep.PICKING_METHOD


async def test_preview_add_synonym_awaits_a_synonym_message(session_factory) -> None:
    _staged_setup_game(session_factory)
    update = _make_preview_callback_update(
        data=dm_start.PREVIEW_ADD_SYNONYM_CALLBACK_DATA, user_id=1
    )
    context = _make_context(session_factory)

    await dm_start.preview_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.callback_query.edit_message_caption.assert_awaited_once()
    with session_factory() as session:
        fetched = session.query(Game).filter_by(starter_id=1).one()
        assert fetched.setup_step == SetupStep.AWAITING_SYNONYM


async def test_search_text_handler_appends_a_synonym_and_reshows_the_preview(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dm_start.pixelate_service, "pixelate", lambda data, stage: b"pixelated")
    _staged_setup_game(session_factory)
    with session_factory() as session:
        game = session.query(Game).filter_by(starter_id=1).one()
        game.setup_step = SetupStep.AWAITING_SYNONYM
        session.commit()

    update = _make_text_update(user_id=1, text="Frieren at the Funeral")
    context = _make_callback_context(session_factory)

    await dm_start.search_text_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.bot.send_photo.assert_awaited_once()
    _, kwargs = context.bot.send_photo.await_args
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

    await dm_start.search_text_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.bot.send_photo.assert_not_awaited()
    update.message.reply_text.assert_awaited_once()
    with session_factory() as session:
        fetched = session.query(Game).filter_by(starter_id=1).one()
        assert fetched.setup_step == SetupStep.AWAITING_SYNONYM


async def test_search_text_handler_ignores_text_while_confirming(session_factory) -> None:
    _staged_setup_game(session_factory)
    update = _make_text_update(user_id=1, text="whatever")
    context = _make_callback_context(session_factory)

    await dm_start.search_text_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

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

    await dm_start.search_text_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_not_awaited()
    context.bot.send_photo.assert_not_awaited()


async def test_photo_handler_updates_the_image_and_reshows_the_preview_when_changing(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dm_start.pixelate_service, "pixelate", lambda data, stage: b"pixelated")
    _staged_setup_game(session_factory)
    with session_factory() as session:
        game = session.query(Game).filter_by(starter_id=1).one()
        game.setup_step = SetupStep.AWAITING_PHOTO_CHANGE
        session.commit()

    update = _make_update(user_id=1, photo_file_id="new-file-456")
    context = _make_callback_context(session_factory)

    await dm_start.photo_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    with session_factory() as session:
        assert session.query(Game).count() == 1  # no duplicate game created
        fetched = session.query(Game).filter_by(starter_id=1).one()
        assert fetched.original_file_id == "new-file-456"
        assert fetched.setup_step == SetupStep.CONFIRMING

    context.bot.get_file.assert_awaited_once_with("new-file-456")
    context.bot.send_photo.assert_awaited_once()
    _, kwargs = context.bot.send_photo.await_args
    assert kwargs["chat_id"] == 1
