from typing import cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start import preview, search
from nani_pix_bot.commands.dm_start.keyboards import (
    ANILIST_METHOD_CALLBACK_DATA,
    MANUAL_METHOD_CALLBACK_DATA,
    PREVIEW_CONFIRM_CALLBACK_DATA,
    RETRY_CALLBACK_DATA,
    SHIKIMORI_METHOD_CALLBACK_DATA,
)
from nani_pix_bot.models.enums import GameStatus, SetupStep
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n
from nani_pix_bot.services.search.anilist import AniListResult
from nani_pix_bot.services.search.shikimori import ShikimoriResult

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
    session_factory,
    *,
    starter_id: int = 1,
    image: bytes | None = b"file123",
    source: str = "anilist",
) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=starter_id))
        session.commit()
        game = game_service.create_setup_game(session, starter_id=starter_id, original_image=image)
        game.source = source
        session.commit()


def _make_method_callback_update(*, data: str, user_id: int = 1) -> MagicMock:
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.from_user.id = user_id
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


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


async def test_method_pick_callback_handler_stores_the_source_and_prompts_for_search(
    session_factory,
) -> None:
    _create_setup_game(session_factory, starter_id=1)
    update = _make_method_callback_update(data=SHIKIMORI_METHOD_CALLBACK_DATA, user_id=1)
    context = _make_context(session_factory)

    await search.method_pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.callback_query.answer.assert_awaited_once()
    update.callback_query.edit_message_text.assert_awaited_once()
    with session_factory() as session:
        fetched = session.query(Game).filter_by(starter_id=1).one()
        assert fetched.source == "shikimori"
        assert fetched.status == GameStatus.SETUP


async def test_method_pick_callback_handler_asks_a_screenshotless_prompt_for_newgame(
    session_factory,
) -> None:
    """A /newgame game has no image yet, so the photo-first wording
    ("what anime is this from?") would be describing an image that
    doesn't exist — see locales' ask_search vs ask_search_newgame."""
    _create_setup_game(session_factory, starter_id=1, image=None)
    update = _make_method_callback_update(data=ANILIST_METHOD_CALLBACK_DATA, user_id=1)
    context = _make_context(session_factory)

    await search.method_pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    text = update.callback_query.edit_message_text.await_args.args[0]
    assert text == i18n.t("dm_start.ask_search_newgame", "en")


async def test_method_pick_callback_handler_asks_the_photo_first_prompt_with_an_image(
    session_factory,
) -> None:
    _create_setup_game(session_factory, starter_id=1)
    update = _make_method_callback_update(data=ANILIST_METHOD_CALLBACK_DATA, user_id=1)
    context = _make_context(session_factory)

    await search.method_pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    text = update.callback_query.edit_message_text.await_args.args[0]
    assert text == i18n.t("dm_start.ask_search", "en")


async def test_method_pick_callback_handler_prompts_for_a_title_when_manual_is_picked(
    session_factory,
) -> None:
    _create_setup_game(session_factory, starter_id=1)
    update = _make_method_callback_update(data=MANUAL_METHOD_CALLBACK_DATA, user_id=1)
    context = _make_context(session_factory)

    await search.method_pick_callback_handler(
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
    monkeypatch.setattr(search.anilist, "search", search_mock)
    update = _make_text_update()
    context = _make_context(session_factory, search_client=MagicMock())

    await search.search_text_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    search_mock.assert_not_awaited()
    update.message.reply_text.assert_not_awaited()


async def test_search_text_handler_sends_a_searching_message_immediately(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The starter should see feedback right away, before the AniList/
    # Shikimori round-trip (which can take a few seconds, more over
    # Shikimori's proxy hop) resolves — see the search-feedback fix.
    _create_setup_game(session_factory, starter_id=1)
    monkeypatch.setattr(search.anilist, "search", AsyncMock(return_value=[]))
    update = _make_text_update(user_id=1)
    context = _make_context(session_factory, search_client=MagicMock())

    await search.search_text_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    update.message.reply_text.assert_awaited_once()
    assert "search" in update.message.reply_text.await_args.args[0].lower()


async def test_search_text_handler_finds_the_pending_game_from_the_db(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Simulates a bot restart between the photo and the search: nothing in
    # user_data, only the DB row — see issue #11.
    _create_setup_game(session_factory, starter_id=1)
    monkeypatch.setattr(search.anilist, "search", AsyncMock(return_value=[]))
    update = _make_text_update(user_id=1)
    context = _make_context(session_factory, search_client=MagicMock())

    await search.search_text_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    status_message = update.message.reply_text.return_value
    status_message.edit_text.assert_awaited_once()
    assert "no" in status_message.edit_text.await_args.args[0].lower()


async def test_search_text_handler_shows_keyboard_on_anilist_results(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create_setup_game(session_factory, starter_id=1, source="anilist")
    monkeypatch.setattr(search.anilist, "search", AsyncMock(return_value=[_FRIEREN]))
    update = _make_text_update(user_id=1)
    context = _make_context(session_factory, search_client=MagicMock())

    await search.search_text_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    status_message = update.message.reply_text.return_value
    status_message.edit_text.assert_awaited_once()
    _, kwargs = status_message.edit_text.await_args
    assert kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "anilist_pick:99"


async def test_search_text_handler_uses_shikimori_when_that_is_the_chosen_source(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create_setup_game(session_factory, starter_id=1, source="shikimori")
    search_mock = AsyncMock(return_value=[_FRIEREN_SHIKIMORI])
    monkeypatch.setattr(search.shikimori, "search", search_mock)
    update = _make_text_update(user_id=1)
    context = _make_context(session_factory, search_client=MagicMock())

    await search.search_text_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    search_mock.assert_awaited_once_with(context.bot_data["search_client"], "frieren")
    status_message = update.message.reply_text.return_value
    _, kwargs = status_message.edit_text.await_args
    assert kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "shikimori_pick:52991"


async def test_search_text_handler_reshows_method_keyboard_when_the_service_errors(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create_setup_game(session_factory, starter_id=1, source="anilist")
    monkeypatch.setattr(search.anilist, "search", AsyncMock(side_effect=httpx.ConnectError("boom")))
    update = _make_text_update(user_id=1)
    context = _make_context(session_factory, search_client=MagicMock())

    await search.search_text_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

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


async def test_search_text_handler_routes_to_screenshot_search_while_resolving_a_provider(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A text message during PICKING_SCREENSHOT, once a screenshot
    provider is being resolved (screenshot_picker_provider set, no image
    yet), is ticket 8's cross-provider "Search again" query — not an
    identification search."""
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.commit()
        game = game_service.create_setup_game(session, starter_id=1)
        game.source = "anilist"
        game.setup_step = SetupStep.PICKING_SCREENSHOT
        game.screenshot_picker_provider = "tmdb"
        session.commit()

    step_mock = AsyncMock()
    monkeypatch.setattr(search, "_screenshot_search_step", step_mock)
    update = _make_text_update(user_id=1, text="Frieren")
    context = _make_context(session_factory, search_client=MagicMock())

    await search.search_text_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    # The whole source menu is handed down, not a bare provider: the
    # step's failure exits re-show it (search.py's session is closed by
    # the time it runs, so it can't build one itself).
    step_mock.assert_awaited_once()
    assert step_mock.await_args is not None
    menu = step_mock.await_args.args[3]
    assert menu.provider == "tmdb"
    assert menu.providers == ["shikimori", "jikan", "tmdb"]


async def test_search_text_handler_routes_to_screenshot_search_even_with_an_old_image_staged(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: preview.py's "Change image" -> "Pick a different
    screenshot" re-enters PICKING_SCREENSHOT while original_image still
    holds the *old* API-sourced screenshot (only replaced once a new
    one is actually picked) — a typed "Wrong anime? Search again"
    correction at that point must still route to _screenshot_search_step,
    not be silently swallowed because an image already exists."""
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.commit()
        game = game_service.create_setup_game(session, starter_id=1, original_image=b"old-pick")
        game.source = "anilist"
        game.setup_step = SetupStep.PICKING_SCREENSHOT
        # The old pick's provenance and the re-opened picker's provider
        # happen to agree here — they are still two separate columns,
        # and it's the picker one this must route on.
        game.screenshot_source = "tmdb"
        game.screenshot_picker_provider = "tmdb"
        session.commit()

    step_mock = AsyncMock()
    monkeypatch.setattr(search, "_screenshot_search_step", step_mock)
    update = _make_text_update(user_id=1, text="Frieren")
    context = _make_context(session_factory, search_client=MagicMock())

    await search.search_text_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    # The whole source menu is handed down, not a bare provider: the
    # step's failure exits re-show it (search.py's session is closed by
    # the time it runs, so it can't build one itself).
    step_mock.assert_awaited_once()
    assert step_mock.await_args is not None
    menu = step_mock.await_args.args[3]
    assert menu.provider == "tmdb"
    assert menu.providers == ["shikimori", "jikan", "tmdb"]


async def test_search_text_handler_ignores_text_while_still_on_the_source_keyboard(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No screenshot picker provider set yet (still choosing a
    provider) — a stray text message shouldn't be treated as a search
    query at all."""
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.commit()
        game = game_service.create_setup_game(session, starter_id=1)
        game.source = "anilist"
        game.setup_step = SetupStep.PICKING_SCREENSHOT
        session.commit()

    step_mock = AsyncMock()
    monkeypatch.setattr(search, "_screenshot_search_step", step_mock)
    update = _make_text_update(user_id=1, text="Frieren")
    context = _make_context(session_factory, search_client=MagicMock())

    await search.search_text_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    step_mock.assert_not_awaited()
    update.message.reply_text.assert_not_awaited()


async def test_search_text_handler_ignores_text_while_browsing_a_same_provider_gallery(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A same-provider gallery has nothing left to resolve — the id came
    straight from identification, and the gallery doesn't even offer
    "Wrong anime? Search again" — so text typed while browsing it is not
    a correction query. It used to be, because the one overloaded
    column was set by *any* source tap."""
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.commit()
        game = game_service.create_setup_game(session, starter_id=1)
        game.source = "shikimori"
        game.shikimori_id = 52991
        game.setup_step = SetupStep.PICKING_SCREENSHOT
        game.screenshot_picker_provider = None
        session.commit()

    step_mock = AsyncMock()
    monkeypatch.setattr(search, "_screenshot_search_step", step_mock)
    update = _make_text_update(user_id=1, text="Frieren")
    context = _make_context(session_factory, search_client=MagicMock())

    await search.search_text_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    step_mock.assert_not_awaited()
    update.message.reply_text.assert_not_awaited()


async def test_pick_callback_handler_retry_does_not_touch_the_database(session_factory) -> None:
    update = _make_callback_update(data=RETRY_CALLBACK_DATA)
    context = _make_callback_context(session_factory)

    await search.pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.callback_query.answer.assert_awaited_once()
    update.callback_query.edit_message_text.assert_awaited_once()
    context.bot.send_photo.assert_not_awaited()


async def test_pick_callback_handler_shows_a_preview_on_a_valid_anilist_pick(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(preview.pixelate_service, "pixelate", lambda data, stage: b"pixelated")
    monkeypatch.setattr(search.anilist, "get_by_id", AsyncMock(return_value=_FRIEREN))
    _create_setup_game(session_factory, starter_id=1, source="anilist")

    update = _make_callback_update(data="anilist_pick:99", user_id=1)
    context = _make_callback_context(session_factory)

    await search.pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.bot.send_media_group.assert_awaited_once()
    _, kwargs = context.bot.send_media_group.await_args
    # The preview goes to the starter's own DM, not the group topic —
    # nothing is posted to the group until they confirm.
    assert kwargs["chat_id"] == 1
    assert "message_thread_id" not in kwargs
    media = kwargs["media"]
    assert len(media) == 5
    assert all(item.media.input_file_content == b"pixelated" for item in media)
    assert "Frieren: Beyond Journey's End" in media[0].caption
    assert all(item.caption is None for item in media[1:])

    context.bot.send_message.assert_awaited_once()
    _, message_kwargs = context.bot.send_message.await_args
    assert message_kwargs["chat_id"] == 1
    assert "reply_markup" in message_kwargs

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
    monkeypatch.setattr(preview.pixelate_service, "pixelate", lambda data, stage: b"pixelated")
    get_by_id_mock = AsyncMock(return_value=_FRIEREN_SHIKIMORI)
    monkeypatch.setattr(search.shikimori, "get_by_id", get_by_id_mock)
    _create_setup_game(session_factory, starter_id=1, source="shikimori")

    update = _make_callback_update(data="shikimori_pick:52991", user_id=1)
    context = _make_callback_context(session_factory)

    await search.pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    get_by_id_mock.assert_awaited_once_with(context.bot_data["search_client"], 52991)
    context.bot.send_media_group.assert_awaited_once()

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
        search.anilist, "get_by_id", AsyncMock(side_effect=httpx.ConnectError("boom"))
    )
    _create_setup_game(session_factory, starter_id=1, source="anilist")

    update = _make_callback_update(data="anilist_pick:99", user_id=1)
    context = _make_callback_context(session_factory)

    await search.pick_callback_handler(
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
    monkeypatch.setattr(preview.pixelate_service, "pixelate", lambda data, stage: b"pixelated")
    get_by_id_mock = AsyncMock(return_value=_FRIEREN)
    monkeypatch.setattr(search.anilist, "get_by_id", get_by_id_mock)
    _create_setup_game(session_factory, starter_id=1)

    update = _make_callback_update(data="anilist_pick:99", user_id=1)
    context = _make_callback_context(session_factory)

    await search.pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    get_by_id_mock.assert_awaited_once_with(context.bot_data["search_client"], 99)
    context.bot.send_media_group.assert_awaited_once()


async def test_pick_callback_handler_preview_lists_every_accepted_answer(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(preview.pixelate_service, "pixelate", lambda data, stage: b"pixelated")
    monkeypatch.setattr(search.anilist, "get_by_id", AsyncMock(return_value=_FRIEREN))
    _create_setup_game(session_factory, starter_id=1)

    update = _make_callback_update(data="anilist_pick:99", user_id=1)
    context = _make_callback_context(session_factory)

    await search.pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    _, media_kwargs = context.bot.send_media_group.await_args
    caption = media_kwargs["media"][0].caption
    # The title itself:
    assert "Frieren: Beyond Journey's End" in caption
    # Every other stored title variant is already an accepted /guess —
    # neither of these is a substring of the display title, so this only
    # passes if they're shown, not just the manually-typed synonyms.
    assert "Sousou no Frieren" in caption
    assert "葬送のフリーレン" in caption
    _, message_kwargs = context.bot.send_message.await_args
    callbacks = [
        button.callback_data
        for row in message_kwargs["reply_markup"].inline_keyboard
        for button in row
    ]
    assert PREVIEW_CONFIRM_CALLBACK_DATA in callbacks
