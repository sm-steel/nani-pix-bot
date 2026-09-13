from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start import preview, screenshot_gallery
from nani_pix_bot.models.enums import SetupStep
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services.search import shikimori, tmdb
from nani_pix_bot.services.search.tmdb import TMDBResult

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


async def test_screenshot_gallery_callback_handler_more_shows_the_next_page(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    urls = [f"https://shikimori.io/x/{i}.jpg" for i in range(8)]
    monkeypatch.setattr(shikimori, "screenshots", AsyncMock(return_value=urls))
    _staged_game(session_factory, shikimori_id=52991)

    update = _make_callback_update(data="screenshot_more:shikimori:5")
    context = _make_context(session_factory)

    await screenshot_gallery.screenshot_gallery_callback_handler(
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
    monkeypatch.setattr(shikimori, "screenshots", AsyncMock(return_value=urls))
    monkeypatch.setattr(preview.pixelate_service, "pixelate", lambda data, stage: b"pixelated")
    game_id = _staged_game(session_factory, shikimori_id=52991)

    update = _make_callback_update(data="screenshot_pick:shikimori:1")
    context = _make_context(session_factory)
    download_response = MagicMock(content=b"real-screenshot-bytes")
    download_response.raise_for_status = MagicMock()
    context.bot_data["search_client"].get = AsyncMock(return_value=download_response)

    await screenshot_gallery.screenshot_gallery_callback_handler(
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


async def test_screenshot_gallery_callback_handler_pick_falls_back_when_the_fetch_is_now_empty(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: a re-fetch (to resolve the tapped index into a real
    URL) that now comes back empty used to be treated as a silent
    stale-index no-op — leaving PICKING_SCREENSHOT in place with no way
    forward, since the source-selection/gallery messages are already
    gone. It must fall back to asking for an upload instead, same as
    the initial fetch finding nothing."""
    monkeypatch.setattr(shikimori, "screenshots", AsyncMock(return_value=[]))
    game_id = _staged_game(session_factory, shikimori_id=52991)

    update = _make_callback_update(data="screenshot_pick:shikimori:0")
    context = _make_context(session_factory)

    await screenshot_gallery.screenshot_gallery_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.setup_step == SetupStep.AWAITING_PHOTO_CHANGE
    update.callback_query.edit_message_text.assert_awaited_once()
    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "screenshots" in text.lower()


async def test_screenshot_gallery_callback_handler_pick_is_a_noop_for_a_stale_index(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A still-successful fetch that just doesn't have the tapped index
    anymore (e.g. the gallery shrank between page loads) stays a silent
    no-op — distinct from the fetch itself failing/emptying above."""
    monkeypatch.setattr(
        shikimori, "screenshots", AsyncMock(return_value=["https://shikimori.io/x/0.jpg"])
    )
    game_id = _staged_game(
        session_factory, shikimori_id=52991, setup_step=SetupStep.PICKING_SCREENSHOT
    )

    update = _make_callback_update(data="screenshot_pick:shikimori:5")
    context = _make_context(session_factory)

    await screenshot_gallery.screenshot_gallery_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.setup_step == SetupStep.PICKING_SCREENSHOT
    update.callback_query.edit_message_text.assert_not_awaited()


async def test_screenshot_gallery_callback_handler_pick_falls_back_when_the_download_fails(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The download step (not the screenshots() listing call) failing —
    a bad URL, a network blip — must also fall back rather than
    propagate as an unhandled exception."""
    monkeypatch.setattr(
        shikimori, "screenshots", AsyncMock(return_value=["https://shikimori.io/x/0.jpg"])
    )
    game_id = _staged_game(session_factory, shikimori_id=52991)

    update = _make_callback_update(data="screenshot_pick:shikimori:0")
    context = _make_context(session_factory)
    context.bot_data["search_client"].get = AsyncMock(side_effect=RuntimeError("boom"))

    await screenshot_gallery.screenshot_gallery_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.setup_step == SetupStep.AWAITING_PHOTO_CHANGE
        assert fetched.original_image is None
    update.callback_query.edit_message_text.assert_awaited_once()


async def test_screenshot_search_again_callback_handler_asks_for_a_query(session_factory) -> None:
    game_id = _staged_game(session_factory, source="anilist", anilist_id=99)
    update = _make_callback_update(data="screenshot_search_again:tmdb")
    context = _make_context(session_factory)

    await screenshot_gallery.screenshot_search_again_callback_handler(
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
    monkeypatch.setattr(tmdb, "search", AsyncMock(return_value=[_FRIEREN_TMDB]))
    context = _make_context(session_factory)
    status_message = MagicMock()
    status_message.edit_text = AsyncMock()
    message = MagicMock()
    message.text = "Frieren"
    message.reply_text = AsyncMock(return_value=status_message)

    await screenshot_gallery._screenshot_search_step(message, context, "en", "tmdb")

    status_message.edit_text.assert_awaited_once()
    assert status_message.edit_text.await_args is not None
    _, kwargs = status_message.edit_text.await_args
    callbacks = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert "screenshot_search_pick:tmdb:209867" in callbacks


async def test_screenshot_search_pick_callback_handler_resolves_and_shows_gallery(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tmdb, "get_by_id", AsyncMock(return_value=_FRIEREN_TMDB))
    monkeypatch.setattr(
        tmdb, "screenshots", AsyncMock(return_value=["https://image.tmdb.org/x/0.jpg"])
    )
    game_id = _staged_game(
        session_factory, source="anilist", anilist_id=99, screenshot_source="tmdb"
    )

    update = _make_callback_update(data="screenshot_search_pick:tmdb:209867")
    context = _make_context(session_factory)

    await screenshot_gallery.screenshot_search_pick_callback_handler(
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
    monkeypatch.setattr(tmdb, "get_by_id", AsyncMock(return_value=None))
    _staged_game(session_factory, source="anilist", anilist_id=99, screenshot_source="tmdb")

    update = _make_callback_update(data="screenshot_search_pick:tmdb:209867")
    context = _make_context(session_factory)

    await screenshot_gallery.screenshot_search_pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.bot.send_media_group.assert_not_awaited()
    update.callback_query.edit_message_text.assert_awaited_once()
