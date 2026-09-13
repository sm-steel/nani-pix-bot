from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start import preview, screenshots
from nani_pix_bot.models.enums import GameStatus, SetupStep
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services.search.shikimori import ShikimoriResult
from nani_pix_bot.services.search.tmdb import TMDBResult

_FRIEREN_SHIKIMORI = ShikimoriResult(
    shikimori_id=52991,
    title_romaji="Sousou no Frieren",
    title_english="Frieren: Beyond Journey's End",
    title_russian="Провожающая в последний путь Фрирен",
    synonyms=[],
)

_FRIEREN_TMDB = TMDBResult(
    tmdb_id=209867,
    title_romaji=None,
    title_english="Frieren: Beyond Journey's End",
    title_native=None,
    synonyms=[],
)


def _make_context(session_factory, **extra_bot_data) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "search_client": MagicMock(),
        "tmdb_client": MagicMock(),
        **extra_bot_data,
    }
    context.bot.send_message = AsyncMock()
    context.bot.send_media_group = AsyncMock()
    return context


def _make_callback_update(*, data: str, user_id: int = 1) -> MagicMock:
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.from_user.id = user_id
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


def _staged_game(session_factory, *, starter_id: int = 1, **overrides) -> int:
    """A SETUP game with identification already staged but no image
    yet — where every /newgame game sits right before the screenshot
    picker."""
    with session_factory() as session:
        session.add(Player(telegram_user_id=starter_id))
        session.commit()
        game = game_service.create_setup_game(session, starter_id=starter_id)
        game.source = overrides.pop("source", "shikimori")
        game.title_english = "Frieren: Beyond Journey's End"
        for key, value in overrides.items():
            setattr(game, key, value)
        session.commit()
        return game.id


async def test_start_screenshot_picker_shows_the_source_keyboard(session_factory) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.commit()
        game = Game(
            starter_id=1,
            status=GameStatus.SETUP,
            source="shikimori",
            shikimori_id=52991,
            title_english="Frieren",
        )
        session.add(game)
        session.commit()
        context = _make_context(session_factory)

        await screenshots.start_screenshot_picker(context, game, "en")

    context.bot.send_message.assert_awaited_once()
    _, kwargs = context.bot.send_message.await_args
    assert kwargs["chat_id"] == 1
    callbacks = [
        button.callback_data for row in kwargs["reply_markup"].inline_keyboard for button in row
    ]
    assert "screenshot_source:shikimori" in callbacks


async def test_start_screenshot_picker_offers_all_three_providers_even_with_no_ids_yet(
    session_factory,
) -> None:
    """Cross-provider resolution (ticket 8) means every screenshot-
    capable provider is offered regardless of the identification
    source — an AniList-identified game still gets Shikimori/Jikan/TMDB
    buttons, auto-searched by title when tapped (see
    _resolve_screenshot_source)."""
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.commit()
        game = Game(starter_id=1, status=GameStatus.SETUP, source="anilist", anilist_id=99)
        session.add(game)
        session.commit()
        context = _make_context(session_factory)

        await screenshots.start_screenshot_picker(context, game, "en")

        assert game.setup_step == SetupStep.PICKING_SCREENSHOT

    context.bot.send_message.assert_awaited_once()
    _, kwargs = context.bot.send_message.await_args
    callbacks = [
        button.callback_data for row in kwargs["reply_markup"].inline_keyboard for button in row
    ]
    assert "screenshot_source:shikimori" in callbacks
    assert "screenshot_source:jikan" in callbacks
    assert "screenshot_source:tmdb" in callbacks


async def test_screenshot_source_callback_handler_shows_the_gallery(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    urls = [f"https://shikimori.io/x/{i}.jpg" for i in range(3)]
    monkeypatch.setattr(screenshots.shikimori, "screenshots", AsyncMock(return_value=urls))
    _staged_game(session_factory, shikimori_id=52991)

    update = _make_callback_update(data="screenshot_source:shikimori")
    context = _make_context(session_factory)

    await screenshots.screenshot_source_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.bot.send_media_group.assert_awaited_once()
    _, kwargs = context.bot.send_media_group.await_args
    assert len(kwargs["media"]) == 3
    context.bot.send_message.assert_awaited_once()
    _, msg_kwargs = context.bot.send_message.await_args
    callbacks = [b.callback_data for row in msg_kwargs["reply_markup"].inline_keyboard for b in row]
    assert "screenshot_pick:shikimori:0" in callbacks
    update.callback_query.edit_message_text.assert_awaited_once()


async def test_screenshot_source_callback_handler_paginates_when_more_than_a_page(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    urls = [f"https://shikimori.io/x/{i}.jpg" for i in range(8)]
    monkeypatch.setattr(screenshots.shikimori, "screenshots", AsyncMock(return_value=urls))
    _staged_game(session_factory, shikimori_id=52991)

    update = _make_callback_update(data="screenshot_source:shikimori")
    context = _make_context(session_factory)

    await screenshots.screenshot_source_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    _, kwargs = context.bot.send_media_group.await_args
    assert len(kwargs["media"]) == screenshots.GALLERY_PAGE_SIZE
    _, msg_kwargs = context.bot.send_message.await_args
    callbacks = [b.callback_data for row in msg_kwargs["reply_markup"].inline_keyboard for b in row]
    assert any(c.startswith("screenshot_more:shikimori:") for c in callbacks)


async def test_screenshot_gallery_callback_handler_more_shows_the_next_page(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    urls = [f"https://shikimori.io/x/{i}.jpg" for i in range(8)]
    monkeypatch.setattr(screenshots.shikimori, "screenshots", AsyncMock(return_value=urls))
    _staged_game(session_factory, shikimori_id=52991)

    update = _make_callback_update(data="screenshot_more:shikimori:5")
    context = _make_context(session_factory)

    await screenshots.screenshot_gallery_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    _, kwargs = context.bot.send_media_group.await_args
    captions = [item.caption for item in kwargs["media"]]
    assert captions == ["6", "7", "8"]  # 1-indexed, starting at offset 5
    update.callback_query.edit_message_text.assert_awaited_once()


async def test_screenshot_gallery_callback_handler_pick_downloads_and_shows_preview(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    urls = ["https://shikimori.io/x/0.jpg", "https://shikimori.io/x/1.jpg"]
    monkeypatch.setattr(screenshots.shikimori, "screenshots", AsyncMock(return_value=urls))
    monkeypatch.setattr(preview.pixelate_service, "pixelate", lambda data, stage: b"pixelated")
    game_id = _staged_game(session_factory, shikimori_id=52991)

    update = _make_callback_update(data="screenshot_pick:shikimori:1")
    context = _make_context(session_factory)
    download_response = MagicMock(content=b"real-screenshot-bytes")
    download_response.raise_for_status = MagicMock()
    context.bot_data["search_client"].get = AsyncMock(return_value=download_response)

    await screenshots.screenshot_gallery_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.original_image == b"real-screenshot-bytes"
        assert fetched.screenshot_source == "shikimori"
        assert fetched.setup_step == SetupStep.CONFIRMING

    context.bot.send_media_group.assert_awaited_once()  # the confirmation preview album
    update.callback_query.edit_message_text.assert_awaited_once()


async def test_screenshot_gallery_callback_handler_pick_is_a_noop_for_a_stale_index(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(screenshots.shikimori, "screenshots", AsyncMock(return_value=[]))
    _staged_game(session_factory, shikimori_id=52991)

    update = _make_callback_update(data="screenshot_pick:shikimori:0")
    context = _make_context(session_factory)

    await screenshots.screenshot_gallery_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.callback_query.edit_message_text.assert_not_awaited()


async def test_screenshot_upload_instead_callback_handler_asks_for_a_photo(
    session_factory,
) -> None:
    game_id = _staged_game(session_factory, shikimori_id=52991)
    update = _make_callback_update(data="screenshot:upload")
    context = _make_context(session_factory)

    await screenshots.screenshot_upload_instead_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.setup_step == SetupStep.AWAITING_PHOTO_CHANGE
    update.callback_query.edit_message_text.assert_awaited_once()


async def test_screenshot_source_callback_handler_cross_provider_auto_resolves_top_result(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Identified via AniList, screenshot requested from Shikimori (no
    shikimori_id on file yet) — ticket 8's silent top-result take."""
    monkeypatch.setattr(
        screenshots.shikimori, "search", AsyncMock(return_value=[_FRIEREN_SHIKIMORI])
    )
    monkeypatch.setattr(
        screenshots.shikimori,
        "screenshots",
        AsyncMock(return_value=["https://shikimori.io/x/0.jpg"]),
    )
    game_id = _staged_game(session_factory, source="anilist", anilist_id=99)

    update = _make_callback_update(data="screenshot_source:shikimori")
    context = _make_context(session_factory)

    await screenshots.screenshot_source_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.shikimori_id == 52991
        assert fetched.source == "anilist"  # identification untouched
        assert fetched.screenshot_source == "shikimori"

    context.bot.send_media_group.assert_awaited_once()
    _, msg_kwargs = context.bot.send_message.await_args
    callbacks = [b.callback_data for row in msg_kwargs["reply_markup"].inline_keyboard for b in row]
    assert "screenshot_search_again:shikimori" in callbacks  # cross_provider=True
    update.callback_query.edit_message_text.assert_awaited_once()


async def test_screenshot_source_callback_handler_cross_provider_no_match_asks_to_search(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(screenshots.shikimori, "search", AsyncMock(return_value=[]))
    game_id = _staged_game(session_factory, source="anilist", anilist_id=99)

    update = _make_callback_update(data="screenshot_source:shikimori")
    context = _make_context(session_factory)

    await screenshots.screenshot_source_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.shikimori_id is None
        assert fetched.screenshot_source == "shikimori"
        assert fetched.original_image is None
    context.bot.send_media_group.assert_not_awaited()
    update.callback_query.edit_message_text.assert_awaited_once()


async def test_screenshot_search_again_callback_handler_asks_for_a_query(session_factory) -> None:
    game_id = _staged_game(session_factory, source="anilist", anilist_id=99)
    update = _make_callback_update(data="screenshot_search_again:tmdb")
    context = _make_context(session_factory)

    await screenshots.screenshot_search_again_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.screenshot_source == "tmdb"
    update.callback_query.edit_message_text.assert_awaited_once()


async def test_screenshot_search_step_shows_results_with_the_cross_search_prefix(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(screenshots.tmdb, "search", AsyncMock(return_value=[_FRIEREN_TMDB]))
    context = _make_context(session_factory)
    status_message = MagicMock()
    status_message.edit_text = AsyncMock()
    message = MagicMock()
    message.text = "Frieren"
    message.reply_text = AsyncMock(return_value=status_message)

    await screenshots._screenshot_search_step(message, context, "en", "tmdb")

    status_message.edit_text.assert_awaited_once()
    assert status_message.edit_text.await_args is not None
    _, kwargs = status_message.edit_text.await_args
    callbacks = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert "screenshot_search_pick:tmdb:209867" in callbacks


async def test_screenshot_search_pick_callback_handler_resolves_and_shows_gallery(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(screenshots.tmdb, "get_by_id", AsyncMock(return_value=_FRIEREN_TMDB))
    monkeypatch.setattr(
        screenshots.tmdb,
        "screenshots",
        AsyncMock(return_value=["https://image.tmdb.org/x/0.jpg"]),
    )
    game_id = _staged_game(
        session_factory, source="anilist", anilist_id=99, screenshot_source="tmdb"
    )

    update = _make_callback_update(data="screenshot_search_pick:tmdb:209867")
    context = _make_context(session_factory)

    await screenshots.screenshot_search_pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.tmdb_id == 209867
        assert fetched.source == "anilist"

    context.bot.send_media_group.assert_awaited_once()
    _, msg_kwargs = context.bot.send_message.await_args
    callbacks = [b.callback_data for row in msg_kwargs["reply_markup"].inline_keyboard for b in row]
    assert "screenshot_search_again:tmdb" in callbacks
    update.callback_query.edit_message_text.assert_awaited_once()


async def test_screenshot_search_pick_callback_handler_handles_a_stale_id(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(screenshots.tmdb, "get_by_id", AsyncMock(return_value=None))
    _staged_game(session_factory, source="anilist", anilist_id=99, screenshot_source="tmdb")

    update = _make_callback_update(data="screenshot_search_pick:tmdb:209867")
    context = _make_context(session_factory)

    await screenshots.screenshot_search_pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.bot.send_media_group.assert_not_awaited()
    update.callback_query.edit_message_text.assert_awaited_once()
