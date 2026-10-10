import asyncio
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start import aliases, preview
from nani_pix_bot.commands.dm_start.keyboards import (
    ALIASES_DONE_CALLBACK_DATA,
    ALIASES_SKIP_CALLBACK_DATA,
    ALIASES_TOGGLE_PREFIX,
)
from nani_pix_bot.models.enums import SetupStep
from nani_pix_bot.models.game import Game
from tests.commands.dm_start.test_preview import (
    _make_callback_context as _base_context,
)
from tests.commands.dm_start.test_preview import (
    _make_preview_callback_update,
    _staged_setup_game,
)


@pytest.fixture(autouse=True)
def _no_pixelation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(preview.pixelate_service, "pixelate", lambda *_: b"pixelated")


def _make_callback_context(session_factory) -> MagicMock:
    """test_preview's context plus a Tenrai client, which the alias search
    asks for and nothing in test_preview does."""
    return _base_context(session_factory, tenrai_client=MagicMock())


def _game(session_factory) -> Game:
    with session_factory() as session:
        game = session.query(Game).one()
        session.expunge(game)
        return game


def _finding(monkeypatch: pytest.MonkeyPatch, result) -> AsyncMock:
    find = (
        AsyncMock(return_value=result)
        if isinstance(result, list)
        else AsyncMock(side_effect=result)
    )
    monkeypatch.setattr(aliases.aliases, "find_aliases", find)
    return find


def _labels(markup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


# --- the background search


async def test_found_names_are_offered_before_the_photo_first_preview(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _finding(monkeypatch, ["Frieren at the Funeral", "Провожающая в последний путь Фрирен"])
    game_id = _staged_setup_game(session_factory, setup_step=SetupStep.PICKING_METHOD)
    context = _make_callback_context(session_factory)

    await aliases._search_and_store(context, game_id, "en")

    game = _game(session_factory)
    assert game.setup_step is SetupStep.PICKING_ALIASES
    assert [s["text"] for s in game.alias_suggestions or []] == [
        "Frieren at the Funeral",
        "Провожающая в последний путь Фрирен",
    ]
    context.bot.send_media_group.assert_not_awaited()
    markup = context.bot.send_message.call_args.kwargs["reply_markup"]
    assert "☐ Frieren at the Funeral" in _labels(markup)


@pytest.mark.parametrize("failure", [RuntimeError("boom"), KeyError("client"), []])
async def test_a_failed_or_empty_search_still_shows_the_preview(
    session_factory, monkeypatch: pytest.MonkeyPatch, failure
) -> None:
    _finding(monkeypatch, failure)
    game_id = _staged_setup_game(session_factory, setup_step=SetupStep.PICKING_METHOD)
    context = _make_callback_context(session_factory)

    await aliases._search_and_store(context, game_id, "en")

    assert _game(session_factory).setup_step is SetupStep.CONFIRMING
    context.bot.send_media_group.assert_awaited_once()


async def test_a_slow_search_is_given_up_and_the_preview_shown(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def never(*_args):
        await asyncio.sleep(60)

    monkeypatch.setattr(aliases.aliases, "find_aliases", never)
    monkeypatch.setattr(aliases, "ALIAS_SEARCH_TIMEOUT", 0.01)
    game_id = _staged_setup_game(session_factory, setup_step=SetupStep.PICKING_METHOD)
    context = _make_callback_context(session_factory)

    await aliases._search_and_store(context, game_id, "en")

    context.bot.send_media_group.assert_awaited_once()


async def test_on_newgame_the_names_wait_for_the_screenshot_pick(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _finding(monkeypatch, ["Frieren at the Funeral"])
    game_id = _staged_setup_game(session_factory, setup_step=SetupStep.PICKING_SCREENSHOT)
    context = _make_callback_context(session_factory)

    await aliases._search_and_store(context, game_id, None)

    game = _game(session_factory)
    assert game.setup_step is SetupStep.PICKING_SCREENSHOT
    assert game.alias_suggestions == [{"text": "Frieren at the Funeral", "selected": False}]
    context.bot.send_message.assert_not_awaited()


async def test_names_found_after_the_preview_are_dropped(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _finding(monkeypatch, ["Frieren at the Funeral"])
    game_id = _staged_setup_game(session_factory)  # already CONFIRMING
    context = _make_callback_context(session_factory)

    await aliases._search_and_store(context, game_id, None)

    assert _game(session_factory).alias_suggestions is None


async def test_names_for_a_previous_identification_are_dropped(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_id = _staged_setup_game(session_factory, setup_step=SetupStep.PICKING_SCREENSHOT)

    async def reidentified(*_args):
        with session_factory() as session:
            session.query(Game).one().title_english = "Something Else"
            session.commit()
        return ["Frieren at the Funeral"]

    monkeypatch.setattr(aliases.aliases, "find_aliases", reidentified)

    await aliases._search_and_store(_make_callback_context(session_factory), game_id, None)

    assert _game(session_factory).alias_suggestions is None


# --- the checkboxes

_OFFERED = [
    {"text": "Frieren at the Funeral", "selected": False},
    {"text": "Sousou no Furiiren", "selected": False},
]


def _picking(session_factory, suggestions=None) -> int:
    return _staged_setup_game(
        session_factory,
        setup_step=SetupStep.PICKING_ALIASES,
        alias_suggestions=suggestions if suggestions is not None else _OFFERED,
    )


async def _tap(session_factory, data: str):
    update = _make_preview_callback_update(data=data)
    update.callback_query.edit_message_reply_markup = AsyncMock()
    context = _make_callback_context(session_factory)
    await aliases.aliases_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )
    return update, context


async def test_a_checkbox_tap_ticks_the_name_and_redraws_the_buttons(session_factory) -> None:
    _picking(session_factory)

    update, _ = await _tap(session_factory, f"{ALIASES_TOGGLE_PREFIX}1")

    assert [s["selected"] for s in _game(session_factory).alias_suggestions or []] == [False, True]
    markup = update.callback_query.edit_message_reply_markup.call_args.kwargs["reply_markup"]
    assert "☑ Sousou no Furiiren" in _labels(markup)
    assert any("(1)" in label for label in _labels(markup))


async def test_add_selected_accepts_the_ticked_names_and_shows_the_preview(
    session_factory,
) -> None:
    _picking(session_factory, [{**_OFFERED[0], "selected": True}, _OFFERED[1]])

    update, context = await _tap(session_factory, ALIASES_DONE_CALLBACK_DATA)

    game = _game(session_factory)
    assert game.synonyms == ["Frieren", "Frieren at the Funeral"]
    assert game.alias_suggestions == []
    assert game.setup_step is SetupStep.CONFIRMING
    context.bot.send_media_group.assert_awaited_once()
    assert "1" in update.callback_query.edit_message_text.call_args.kwargs["text"]


async def test_skip_accepts_nothing(session_factory) -> None:
    _picking(session_factory, [{**_OFFERED[0], "selected": True}])

    _, context = await _tap(session_factory, ALIASES_SKIP_CALLBACK_DATA)

    game = _game(session_factory)
    assert game.synonyms == ["Frieren"]
    assert game.setup_step is SetupStep.CONFIRMING
    context.bot.send_media_group.assert_awaited_once()


async def test_an_out_of_range_checkbox_changes_nothing(session_factory) -> None:
    _picking(session_factory)

    update, _ = await _tap(session_factory, f"{ALIASES_TOGGLE_PREFIX}9")

    assert _game(session_factory).alias_suggestions == _OFFERED
    update.callback_query.edit_message_reply_markup.assert_not_awaited()


async def test_a_stale_tap_is_rejected(session_factory) -> None:
    _staged_setup_game(session_factory)  # CONFIRMING, nothing on offer

    update, context = await _tap(session_factory, ALIASES_DONE_CALLBACK_DATA)

    update.callback_query.answer.assert_awaited_once()
    assert update.callback_query.answer.call_args.kwargs.get("show_alert") is True
    context.bot.send_media_group.assert_not_awaited()
