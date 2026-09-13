from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start import screenshots
from nani_pix_bot.models.enums import GameStatus, SetupStep
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services.search.shikimori import ShikimoriResult

_FRIEREN_SHIKIMORI = ShikimoriResult(
    shikimori_id=52991,
    title_romaji="Sousou no Frieren",
    title_english="Frieren: Beyond Journey's End",
    title_russian="Провожающая в последний путь Фрирен",
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


async def test_screenshot_source_callback_handler_falls_back_when_the_fetch_fails(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: a screenshot-fetch failure (service down) after a
    provider id is already in hand — same-provider here — used to
    propagate as an unhandled exception with no reply at all. Falls
    back to asking for an upload instead, distinct wording from a
    genuinely empty result."""
    monkeypatch.setattr(
        screenshots.shikimori, "screenshots", AsyncMock(side_effect=RuntimeError("boom"))
    )
    game_id = _staged_game(session_factory, shikimori_id=52991)

    update = _make_callback_update(data="screenshot_source:shikimori")
    context = _make_context(session_factory)

    await screenshots.screenshot_source_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.setup_step == SetupStep.AWAITING_PHOTO_CHANGE
    context.bot.send_media_group.assert_not_awaited()
    update.callback_query.edit_message_text.assert_awaited_once()
    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "screenshots" in text.lower()
