"""Adding to the author's clue screenshot list from a source gallery
(issue #452), and re-identifying the anime clearing it."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import httpx
from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start import clue_gallery, clue_panel, preview, repick
from nani_pix_bot.models import ClueAuthorScreenshot
from nani_pix_bot.models.enums import ClueScreenshotMode, Provider, SetupStep
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services.clues import author_list
from nani_pix_bot.services.pixelate import fingerprint
from nani_pix_bot.services.search import shikimori
from nani_pix_bot.services.search.anilist import AniListResult
from tests.frames import frame_bytes

STARTER = 1
_MAIN = frame_bytes(0)
_URLS = [f"https://shiki/{n}.jpg" for n in range(8)]
_FRIEREN = AniListResult(
    anilist_id=99,
    title_romaji="Sousou no Frieren",
    title_english="Frieren: Beyond Journey's End",
    title_native="葬送のフリーレン",
    synonyms=["Frieren"],
    year=2023,
)


def _context(session_factory, *, download: bytes | Exception | None = None) -> MagicMock:
    context = MagicMock()
    context.bot_data = {"session_factory": session_factory, "search_client": MagicMock()}
    context.bot.get_chat_member = AsyncMock(return_value=MagicMock(status=ChatMemberStatus.MEMBER))
    context.bot.send_message = AsyncMock()
    context.bot.send_media_group = AsyncMock()
    if isinstance(download, Exception):
        context.bot_data["search_client"].get = AsyncMock(side_effect=download)
    else:
        response = MagicMock(content=download if download is not None else frame_bytes(3))
        response.raise_for_status = MagicMock()
        context.bot_data["search_client"].get = AsyncMock(return_value=response)
    return context


def _staged(session_factory, **overrides) -> int:
    with session_factory() as session:
        session.add(Player(telegram_user_id=STARTER))
        session.commit()
        game = game_service.create_setup_game(session, starter_id=STARTER, original_image=_MAIN)
        game_service.stage_result(game, _FRIEREN, source=Provider.ANILIST)
        game.shikimori_id = 52991
        game.screenshot_source = Provider.SHIKIMORI
        game.shown_screenshot_urls = [_URLS[0]]
        game.setup_step = SetupStep.CONFIRMING
        for key, value in overrides.items():
            setattr(game, key, value)
        session.commit()
        return game.id


def _query_update(data: str) -> MagicMock:
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.from_user.id = STARTER
    update.callback_query.from_user.full_name = "Starter Name"
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.edit_message_reply_markup = AsyncMock()
    return update


async def _tap(handler, context, data: str) -> MagicMock:
    update = _query_update(data)
    await handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))
    return update.callback_query


def _buttons(markup: InlineKeyboardMarkup | None) -> dict[str, str]:
    assert markup is not None
    return {
        str(button.callback_data): button.text for row in markup.inline_keyboard for button in row
    }


def _game(session_factory, game_id: int) -> Game:
    with session_factory() as session:
        game = session.get(Game, game_id)
        assert game is not None
        _ = game.original_image
        return game


def _entries(session_factory, game_id: int) -> list[ClueAuthorScreenshot]:
    with session_factory() as session:
        return author_list.entries(session, game_id)


def _add_entry(session_factory, game_id: int, seed: int, url: str | None) -> None:
    with session_factory() as session:
        image = frame_bytes(seed)
        session.add(
            ClueAuthorScreenshot(
                game_id=game_id,
                source=Provider.SHIKIMORI if url else None,
                url=url,
                image=image,
                fingerprint=fingerprint.fingerprint(image),
            )
        )
        session.commit()


def _stub_urls(monkeypatch, urls: list[str] | Exception) -> AsyncMock:
    if isinstance(urls, Exception):
        stub = AsyncMock(side_effect=urls)
    else:
        stub = AsyncMock(return_value=list(urls))
    monkeypatch.setattr(shikimori, "screenshots", stub)
    return stub


async def test_panel_offers_add_from_a_source(session_factory) -> None:
    _staged(session_factory)

    query = await _tap(
        clue_panel.clue_panel_callback_handler, _context(session_factory), "cluepanel:open"
    )

    assert "cluepanel:source" in _buttons(query.edit_message_text.await_args.kwargs["reply_markup"])


async def test_source_menu_offers_shikimori_and_tmdb_only(session_factory) -> None:
    _staged(session_factory, tenrai_id=5, tmdb_id=7)

    query = await _tap(
        clue_panel.clue_panel_callback_handler, _context(session_factory), "cluepanel:source"
    )

    buttons = _buttons(query.edit_message_text.await_args.kwargs["reply_markup"])
    assert "cluegal:src:shikimori" in buttons
    assert "cluegal:src:tmdb" in buttons
    assert "cluegal:src:tenrai" not in buttons
    assert "cluepanel:open" in buttons


async def test_source_menu_without_a_source_offers_back(session_factory) -> None:
    _staged(session_factory, shikimori_id=None, screenshot_source=None)

    query = await _tap(
        clue_panel.clue_panel_callback_handler, _context(session_factory), "cluepanel:source"
    )

    kwargs = query.edit_message_text.await_args.kwargs
    assert "no Shikimori or TMDB" in kwargs["text"]
    assert list(_buttons(kwargs["reply_markup"])) == ["cluepanel:open"]


async def test_gallery_excludes_main_and_listed_urls(session_factory, monkeypatch) -> None:
    game_id = _staged(session_factory)
    _add_entry(session_factory, game_id, 1, _URLS[2])
    _stub_urls(monkeypatch, _URLS)
    context = _context(session_factory)

    await _tap(clue_gallery.clue_gallery_callback_handler, context, "cluegal:src:shikimori")

    media = context.bot.send_media_group.await_args.kwargs["media"]
    assert [item.media for item in media] == [_URLS[1], _URLS[3], _URLS[4], _URLS[5], _URLS[6]]
    assert [item.caption for item in media] == ["1", "2", "3", "4", "5"]
    buttons = _buttons(context.bot.send_message.await_args.kwargs["reply_markup"])
    # Each number picks by its position in the provider's own list.
    assert buttons["cluegal:pick:shikimori:1"] == "1"
    assert buttons["cluegal:pick:shikimori:3"] == "2"
    assert "cluegal:more:shikimori:5" in buttons
    assert "cluepanel:open" in buttons


async def test_gallery_more_shows_the_next_page(session_factory, monkeypatch) -> None:
    _staged(session_factory)
    _stub_urls(monkeypatch, _URLS)
    context = _context(session_factory)

    await _tap(clue_gallery.clue_gallery_callback_handler, context, "cluegal:more:shikimori:5")

    media = context.bot.send_media_group.await_args.kwargs["media"]
    assert [item.media for item in media] == [_URLS[6], _URLS[7]]
    assert [item.caption for item in media] == ["6", "7"]
    buttons = _buttons(context.bot.send_message.await_args.kwargs["reply_markup"])
    assert not any(data.startswith("cluegal:more:") for data in buttons)


async def test_pick_adds_entry_keeps_main_screenshot(session_factory, monkeypatch) -> None:
    game_id = _staged(session_factory)
    _stub_urls(monkeypatch, _URLS)
    context = _context(session_factory, download=frame_bytes(3))

    query = await _tap(
        clue_gallery.clue_gallery_callback_handler, context, "cluegal:pick:shikimori:3"
    )

    [entry] = _entries(session_factory, game_id)
    assert (entry.source, entry.url) == (Provider.SHIKIMORI, _URLS[3])
    game = _game(session_factory, game_id)
    assert game.original_image == _MAIN
    assert game.screenshot_source == Provider.SHIKIMORI
    assert game.shown_screenshot_urls == [_URLS[0]]
    assert game.setup_step is SetupStep.CONFIRMING
    kwargs = query.edit_message_text.await_args.kwargs
    assert "Added" in kwargs["text"]
    assert "1/10" in kwargs["text"]
    assert "cluepanel:remove" in _buttons(kwargs["reply_markup"])


async def test_pick_of_the_main_frame_is_refused(session_factory, monkeypatch) -> None:
    game_id = _staged(session_factory)
    _stub_urls(monkeypatch, _URLS)
    context = _context(session_factory, download=_MAIN)

    query = await _tap(
        clue_gallery.clue_gallery_callback_handler, context, "cluegal:pick:shikimori:4"
    )

    assert _entries(session_factory, game_id) == []
    assert "same frame" in query.edit_message_text.await_args.kwargs["text"]


async def test_gallery_provider_down_back_to_panel(session_factory, monkeypatch) -> None:
    game_id = _staged(session_factory)
    _stub_urls(monkeypatch, httpx.ConnectError("down"))
    context = _context(session_factory)

    query = await _tap(clue_gallery.clue_gallery_callback_handler, context, "cluegal:src:shikimori")

    kwargs = query.edit_message_text.await_args.kwargs
    assert "isn't responding" in kwargs["text"]
    assert list(_buttons(kwargs["reply_markup"])) == ["cluepanel:open"]
    context.bot.send_media_group.assert_not_awaited()
    game = _game(session_factory, game_id)
    assert (game.setup_step, game.original_image) == (SetupStep.CONFIRMING, _MAIN)


async def test_pick_download_failure_back_to_panel(session_factory, monkeypatch) -> None:
    game_id = _staged(session_factory)
    _stub_urls(monkeypatch, _URLS)
    context = _context(session_factory, download=httpx.ConnectError("down"))

    query = await _tap(
        clue_gallery.clue_gallery_callback_handler, context, "cluegal:pick:shikimori:3"
    )

    kwargs = query.edit_message_text.await_args.kwargs
    assert "isn't responding" in kwargs["text"]
    assert list(_buttons(kwargs["reply_markup"])) == ["cluepanel:open"]
    assert _entries(session_factory, game_id) == []
    assert _game(session_factory, game_id).setup_step is SetupStep.CONFIRMING


async def test_pick_out_of_range_is_stale(session_factory, monkeypatch, log_records) -> None:
    game_id = _staged(session_factory)
    _stub_urls(monkeypatch, _URLS)
    context = _context(session_factory)

    query = await _tap(
        clue_gallery.clue_gallery_callback_handler, context, "cluegal:pick:shikimori:99"
    )

    query.edit_message_reply_markup.assert_awaited_once_with(reply_markup=None)
    assert _entries(session_factory, game_id) == []
    assert any(line.level == "WARNING" for line in log_records)


async def test_gallery_tap_after_confirm_drops_buttons(
    session_factory, monkeypatch, log_records
) -> None:
    _staged(session_factory, setup_step=SetupStep.PICKING_METHOD)
    stub = _stub_urls(monkeypatch, _URLS)
    context = _context(session_factory)

    query = await _tap(clue_gallery.clue_gallery_callback_handler, context, "cluegal:src:shikimori")

    query.edit_message_reply_markup.assert_awaited_once_with(reply_markup=None)
    stub.assert_not_awaited()
    assert any(line.level == "WARNING" for line in log_records)


async def test_restart_identification_clears_clue_list(session_factory) -> None:
    game_id = _staged(session_factory, clue_screenshot_mode=ClueScreenshotMode.AUTHOR)
    _add_entry(session_factory, game_id, 1, None)

    await _tap(preview.preview_callback_handler, _context(session_factory), "preview:research")

    assert _entries(session_factory, game_id) == []
    assert _game(session_factory, game_id).clue_screenshot_mode == ClueScreenshotMode.RANDOM


async def test_repick_anime_clears_clue_list(session_factory) -> None:
    game_id = _staged(session_factory, clue_screenshot_mode=ClueScreenshotMode.BOTH)
    _add_entry(session_factory, game_id, 1, _URLS[1])

    await _tap(repick.repick_callback_handler, _context(session_factory), "repick:anime")

    assert _entries(session_factory, game_id) == []
    assert _game(session_factory, game_id).clue_screenshot_mode == ClueScreenshotMode.RANDOM
