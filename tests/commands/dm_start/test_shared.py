import threading
from unittest.mock import AsyncMock, MagicMock

import pytest

from nani_pix_bot.commands.dm_start import _shared
from nani_pix_bot.commands.dm_start._shared import _search_and_build_keyboard
from nani_pix_bot.models.enums import SetupStep
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import game as game_service


async def test_search_and_build_keyboard_returns_results_and_the_built_keyboard() -> None:
    client = MagicMock()
    keyboard = MagicMock()
    search_fn = AsyncMock(return_value=["a", "b"])
    keyboard_fn = MagicMock(return_value=keyboard)

    results, built = await _search_and_build_keyboard(client, "frieren", search_fn, keyboard_fn)

    search_fn.assert_awaited_once_with(client, "frieren")
    keyboard_fn.assert_called_once_with(["a", "b"])
    assert results == ["a", "b"]
    assert built is keyboard


async def test_search_and_build_keyboard_returns_no_keyboard_for_empty_results() -> None:
    client = MagicMock()
    search_fn = AsyncMock(return_value=[])
    keyboard_fn = MagicMock()

    results, built = await _search_and_build_keyboard(client, "zzzz", search_fn, keyboard_fn)

    keyboard_fn.assert_not_called()
    assert results == []
    assert built is None


async def test_search_and_build_keyboard_propagates_a_search_failure() -> None:
    client = MagicMock()
    search_fn = AsyncMock(side_effect=RuntimeError("boom"))
    keyboard_fn = MagicMock()

    with pytest.raises(RuntimeError, match="boom"):
        await _search_and_build_keyboard(client, "frieren", search_fn, keyboard_fn)

    keyboard_fn.assert_not_called()


def _setup_game(session):
    session.add(Player(telegram_user_id=1))
    session.flush()
    game = game_service.create_setup_game(session, starter_id=1, original_image=b"raw")
    game.title_english = "Frieren"
    session.flush()
    return game


def test_staging_the_preview_does_not_pixelate(session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Issue #161: rendering five stages blocks the event loop for every
    chat, so staging (inside the session, on the loop) only captures what
    rendering needs."""

    def refuse(*_args):
        raise AssertionError("pixelated while staging")

    monkeypatch.setattr(_shared.pixelate_service, "pixelate", refuse)
    game = _setup_game(session)

    album = _shared._stage_preview(session, game, "en")

    assert game.setup_step is SetupStep.CONFIRMING
    assert album.image == b"raw"
    assert len(album.widths) == 5


async def test_the_preview_album_renders_off_the_event_loop(
    session, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop_thread = threading.get_ident()
    render_threads: list[int] = []

    def fake_pixelate(image: bytes, width: int, _algorithm) -> bytes:
        render_threads.append(threading.get_ident())
        return image + str(width).encode()

    monkeypatch.setattr(_shared.pixelate_service, "pixelate", fake_pixelate)
    album = _shared._stage_preview(session, _setup_game(session), "en")
    context = MagicMock()
    context.bot.send_media_group = AsyncMock()
    context.bot.send_message = AsyncMock()

    await _shared._post_preview_album(context, album, "en")

    media = context.bot.send_media_group.await_args_list[0].kwargs["media"]
    assert len(media) == 5
    assert media[0].caption
    assert all(m.caption is None for m in media[1:])
    assert render_threads
    assert loop_thread not in render_threads
