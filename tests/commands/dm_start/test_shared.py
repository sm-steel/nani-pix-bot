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
    assert isinstance(album, _shared._PreviewAlbum)
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


def test_a_number_heavy_title_asks_whether_numbers_matter_first(session) -> None:
    game = _setup_game(session)
    game.title_english = "91 Days"

    staged = _shared._stage_preview(session, game, "en")

    assert game.setup_step is SetupStep.ASKING_NUMBERS
    assert isinstance(staged, _shared._NumbersQuestion)
    assert "91 Days" in staged.text


def test_an_answered_numbers_question_goes_straight_to_the_preview(session) -> None:
    game = _setup_game(session)
    game.title_english = "91 Days"
    game.numbers_matter = False

    staged = _shared._stage_preview(session, game, "en")

    assert game.setup_step is SetupStep.CONFIRMING
    assert isinstance(staged, _shared._PreviewAlbum)
    assert staged.numbers_matter is False


def test_the_preview_switch_is_left_out_when_no_title_has_a_number(session) -> None:
    staged = _shared._stage_preview(session, _setup_game(session), "en")

    assert isinstance(staged, _shared._PreviewAlbum)
    assert staged.numbers_matter is None


async def test_the_numbers_question_is_sent_instead_of_the_album() -> None:
    context = MagicMock()
    context.bot.send_media_group = AsyncMock()
    context.bot.send_message = AsyncMock()

    await _shared._post_preview_album(context, _shared._NumbersQuestion(1, "question?"), "en")

    context.bot.send_media_group.assert_not_awaited()
    sent = context.bot.send_message.await_args_list[0].kwargs
    assert sent["text"] == "question?"
    data = [b.callback_data for row in sent["reply_markup"].inline_keyboard for b in row]
    assert data == ["numbers:no", "numbers:yes"]


def test_resuming_on_the_numbers_question_reshows_it(session) -> None:
    game = _setup_game(session)
    game.title_english = "91 Days"
    game.setup_step = SetupStep.ASKING_NUMBERS

    text, keyboard = _shared._current_setup_screen(game, "en", MagicMock())

    assert "91 Days" in text
    assert keyboard is not None


def test_every_setup_step_has_a_screen_to_resume_on() -> None:
    assert set(_shared._SETUP_SCREENS) == set(SetupStep)
