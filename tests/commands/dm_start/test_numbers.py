from typing import cast

import pytest
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start import numbers, preview
from nani_pix_bot.commands.dm_start.keyboards import (
    NUMBERS_NO_CALLBACK_DATA,
    NUMBERS_YES_CALLBACK_DATA,
    PREVIEW_NUMBERS_CALLBACK_DATA,
)
from nani_pix_bot.models.enums import SetupStep
from nani_pix_bot.models.game import Game
from tests.commands.dm_start.test_preview import (
    _make_callback_context,
    _make_preview_callback_update,
    _staged_setup_game,
)


def _stored(session_factory) -> tuple[bool | None, SetupStep]:
    with session_factory() as session:
        game = session.query(Game).one()
        return game.numbers_matter, game.setup_step


async def _answer(session_factory, data: str):
    update = _make_preview_callback_update(data=data)
    context = _make_callback_context(session_factory)
    await numbers.numbers_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )
    return update, context


@pytest.fixture(autouse=True)
def _no_pixelation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(preview.pixelate_service, "pixelate", lambda *_: b"pixelated")


@pytest.mark.parametrize(
    ("data", "expected"), [(NUMBERS_YES_CALLBACK_DATA, True), (NUMBERS_NO_CALLBACK_DATA, False)]
)
async def test_answering_stores_it_and_shows_the_preview(session_factory, data, expected) -> None:
    _staged_setup_game(
        session_factory, title_english="91 Days", setup_step=SetupStep.ASKING_NUMBERS
    )

    update, context = await _answer(session_factory, data)

    assert _stored(session_factory) == (expected, SetupStep.CONFIRMING)
    context.bot.send_media_group.assert_awaited_once()
    assert "reply_markup" not in update.callback_query.edit_message_text.call_args.kwargs
    markup = context.bot.send_message.call_args.kwargs["reply_markup"]
    labels = [row[0].text for row in markup.inline_keyboard]
    assert any(("✅" if expected else "⬜") in label and "🔢" in label for label in labels)


async def test_a_stale_answer_changes_nothing(session_factory) -> None:
    _staged_setup_game(session_factory, title_english="91 Days")

    update, context = await _answer(session_factory, NUMBERS_YES_CALLBACK_DATA)

    assert _stored(session_factory) == (None, SetupStep.CONFIRMING)
    context.bot.send_media_group.assert_not_awaited()
    update.callback_query.answer.assert_awaited_once()


async def test_an_unknown_answer_is_ignored(session_factory) -> None:
    _staged_setup_game(
        session_factory, title_english="91 Days", setup_step=SetupStep.ASKING_NUMBERS
    )

    await _answer(session_factory, "numbers:maybe")

    assert _stored(session_factory) == (None, SetupStep.ASKING_NUMBERS)


async def test_the_preview_switch_flips_numbers_matter(session_factory) -> None:
    _staged_setup_game(session_factory, title_english="Mob Psycho 100")
    update = _make_preview_callback_update(data=PREVIEW_NUMBERS_CALLBACK_DATA)
    context = _make_callback_context(session_factory)

    await preview.preview_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )
    assert _stored(session_factory)[0] is True
    await preview.preview_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )
    assert _stored(session_factory)[0] is False

    markup = update.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
    assert PREVIEW_NUMBERS_CALLBACK_DATA in [
        b.callback_data for row in markup.inline_keyboard for b in row
    ]


async def test_the_preview_has_no_switch_when_no_title_has_a_number(session_factory) -> None:
    _staged_setup_game(session_factory)
    update = _make_preview_callback_update(data="preview:algo:back")
    context = _make_callback_context(session_factory)

    await preview.preview_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    markup = update.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
    data = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert PREVIEW_NUMBERS_CALLBACK_DATA not in data
