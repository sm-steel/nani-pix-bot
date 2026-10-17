"""The setup preview's clue screenshot panel (issue #452): the mode, uploads
and removals of the author's clue screenshot list."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start import _shared, clue_panel, intake
from nani_pix_bot.models import ClueAuthorScreenshot
from nani_pix_bot.models.enums import ClueScreenshotMode, GameStatus, Provider, SetupStep
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services.clues import author_list
from nani_pix_bot.services.pixelate import fingerprint
from nani_pix_bot.services.search.anilist import AniListResult
from tests.frames import frame_bytes

STARTER = 1
_MAIN = frame_bytes(0)
_FRIEREN = AniListResult(
    anilist_id=99,
    title_romaji="Sousou no Frieren",
    title_english="Frieren: Beyond Journey's End",
    title_native="葬送のフリーレン",
    synonyms=["Frieren"],
    year=2023,
)


def _context(session_factory, upload: bytes = b"") -> MagicMock:
    context = MagicMock()
    context.bot_data = {"session_factory": session_factory, "group_chat_id": 555}
    context.bot.get_chat_member = AsyncMock(return_value=MagicMock(status=ChatMemberStatus.MEMBER))
    context.bot.send_message = AsyncMock()
    context.bot.get_file = AsyncMock()
    context.bot.get_file.return_value.download_as_bytearray = AsyncMock(
        return_value=bytearray(upload)
    )
    context.job_queue.get_jobs_by_name.return_value = []
    return context


def _query_update(data: str, user_id: int = STARTER) -> MagicMock:
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.from_user.id = user_id
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.edit_message_reply_markup = AsyncMock()
    return update


def _photo_update(user_id: int = STARTER) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.username = "starter"
    update.effective_user.full_name = "Starter Name"
    update.effective_chat.type = "private"
    update.message.photo = [MagicMock(file_id="photo-1")]
    update.message.reply_text = AsyncMock()
    return update


def _staged(session_factory, **overrides) -> int:
    with session_factory() as session:
        session.add(Player(telegram_user_id=STARTER))
        session.commit()
        game = game_service.create_setup_game(session, starter_id=STARTER, original_image=_MAIN)
        game_service.stage_result(game, _FRIEREN, source=Provider.ANILIST)
        game.setup_step = SetupStep.CONFIRMING
        for key, value in overrides.items():
            setattr(game, key, value)
        session.commit()
        return game.id


def _game(session_factory, game_id: int) -> Game:
    with session_factory() as session:
        game = session.get(Game, game_id)
        assert game is not None
        _ = game.original_image  # load the deferred column before the session closes
        return game


def _entries(session_factory, game_id: int) -> list[ClueAuthorScreenshot]:
    with session_factory() as session:
        return author_list.entries(session, game_id)


async def _tap(context, data: str) -> MagicMock:
    update = _query_update(data)
    await clue_panel.clue_panel_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )
    return update.callback_query


async def _send_photo(context) -> MagicMock:
    update = _photo_update()
    await intake.photo_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))
    return update


def _buttons(markup: InlineKeyboardMarkup | None) -> dict[str, str]:
    assert markup is not None
    return {
        str(button.callback_data): button.text for row in markup.inline_keyboard for button in row
    }


def _edited_markup(query: MagicMock) -> InlineKeyboardMarkup:
    return query.edit_message_text.await_args.kwargs["reply_markup"]


def _sent(context) -> tuple[str, InlineKeyboardMarkup]:
    kwargs = context.bot.send_message.await_args.kwargs
    return kwargs["text"], kwargs["reply_markup"]


def test_preview_shows_clue_button_with_mode(session_factory) -> None:
    game_id = _staged(session_factory, clue_screenshot_mode=ClueScreenshotMode.BOTH)

    _, markup = _shared._confirming_screen(_game(session_factory, game_id), "en", MagicMock())

    assert "Both" in _buttons(markup)["cluepanel:open"]


async def test_preview_album_follow_up_carries_the_clue_button(
    session_factory, monkeypatch
) -> None:
    game_id = _staged(session_factory)
    monkeypatch.setattr(_shared, "_render_preview", lambda _album: [])
    context = _context(session_factory)
    context.bot.send_media_group = AsyncMock()
    with session_factory() as session:
        game = session.get(Game, game_id)
        assert game is not None
        album = _shared._stage_preview(session, game, "en")
        session.commit()

    await _shared._post_preview_album(context, album, "en")

    _, markup = _sent(context)
    assert "Random" in _buttons(markup)["cluepanel:open"]


def test_hard_mode_preview_has_no_clue_button(session_factory) -> None:
    game_id = _staged(session_factory, hard_mode=True)

    _, markup = _shared._confirming_screen(_game(session_factory, game_id), "en", MagicMock())

    assert "cluepanel:open" not in _buttons(markup)


async def test_open_shows_mode_count_and_buttons(session_factory) -> None:
    game_id = _staged(session_factory)
    context = _context(session_factory)

    query = await _tap(context, "cluepanel:open")

    text = query.edit_message_text.await_args.kwargs["text"]
    assert "Random" in text
    assert "0/10" in text
    buttons = _buttons(_edited_markup(query))
    assert buttons["cluepanel:mode:random"].startswith("✅")
    assert not buttons["cluepanel:mode:author"].startswith("✅")
    assert "cluepanel:upload" in buttons
    assert "cluepanel:back" in buttons
    assert "cluepanel:remove" not in buttons  # nothing to remove yet
    assert _game(session_factory, game_id).setup_step is SetupStep.CONFIRMING


async def test_mode_tap_sets_mode(session_factory) -> None:
    game_id = _staged(session_factory)
    context = _context(session_factory)

    query = await _tap(context, "cluepanel:mode:author")

    assert _game(session_factory, game_id).clue_screenshot_mode == ClueScreenshotMode.AUTHOR
    assert _buttons(_edited_markup(query))["cluepanel:mode:author"].startswith("✅")


async def test_unknown_mode_is_ignored(session_factory, log_records) -> None:
    game_id = _staged(session_factory)
    context = _context(session_factory)

    await _tap(context, "cluepanel:mode:bogus")

    assert _game(session_factory, game_id).clue_screenshot_mode == ClueScreenshotMode.RANDOM
    assert any(line.level == "WARNING" for line in log_records)


async def test_upload_then_photo_adds_entry(session_factory) -> None:
    game_id = _staged(session_factory)
    context = _context(session_factory, upload=frame_bytes(1))

    query = await _tap(context, "cluepanel:upload")
    assert _game(session_factory, game_id).setup_step is SetupStep.AWAITING_CLUE_PHOTO
    assert "cluepanel:open" in _buttons(_edited_markup(query))  # a way back
    await _send_photo(context)

    game = _game(session_factory, game_id)
    assert game.setup_step is SetupStep.CONFIRMING
    assert game.original_image == _MAIN
    [entry] = _entries(session_factory, game_id)
    assert (entry.source, entry.url) == (None, None)
    text, markup = _sent(context)
    assert "Added" in text
    assert "1/10" in text
    assert "cluepanel:remove" in _buttons(markup)


async def test_duplicate_upload_replied_not_added(session_factory) -> None:
    game_id = _staged(session_factory)
    context = _context(session_factory, upload=_MAIN)

    await _tap(context, "cluepanel:upload")
    await _send_photo(context)

    assert _entries(session_factory, game_id) == []
    text, _ = _sent(context)
    assert "same frame" in text
    assert _game(session_factory, game_id).setup_step is SetupStep.CONFIRMING


async def test_unreadable_upload_replied_not_added(session_factory) -> None:
    game_id = _staged(session_factory)
    context = _context(session_factory, upload=b"not an image")

    await _tap(context, "cluepanel:upload")
    await _send_photo(context)

    assert _entries(session_factory, game_id) == []
    text, _ = _sent(context)
    assert "couldn't read" in text


async def test_full_list_upload_replied_not_added(session_factory) -> None:
    game_id = _staged(session_factory)
    with session_factory() as session:
        game = session.get(Game, game_id)
        assert game is not None
        for seed in range(1, 11):
            author_list.add(session, game, author_list.NewEntry(frame_bytes(seed)))
        session.commit()
    context = _context(session_factory, upload=frame_bytes(42))

    await _tap(context, "cluepanel:upload")
    await _send_photo(context)

    assert len(_entries(session_factory, game_id)) == 10
    text, _ = _sent(context)
    assert "full" in text


async def test_photo_at_confirming_still_starts_nothing_new(session_factory) -> None:
    """Without the upload tap, a photo at the preview is not a clue."""
    game_id = _staged(session_factory)
    context = _context(session_factory, upload=frame_bytes(1))

    await _send_photo(context)

    assert _entries(session_factory, game_id) == []


def _seed_entries(session_factory, game_id: int, count: int) -> list[int]:
    ids = []
    with session_factory() as session:
        for seed in range(1, count + 1):
            image = frame_bytes(seed)
            row = ClueAuthorScreenshot(
                game_id=game_id,
                source=Provider.SHIKIMORI if seed % 2 else None,
                url=f"https://x/{seed}.jpg" if seed % 2 else None,
                image=image,
                fingerprint=fingerprint.fingerprint(image),
            )
            session.add(row)
            session.flush()
            ids.append(row.id)
        session.commit()
    return ids


async def test_remove_entry(session_factory) -> None:
    game_id = _staged(session_factory)
    first, second = _seed_entries(session_factory, game_id, 2)
    context = _context(session_factory)

    listing = await _tap(context, "cluepanel:remove")
    buttons = _buttons(_edited_markup(listing))
    assert buttons[f"cluepanel:remove:{first}"] == "🗑 1 · Shikimori"
    assert buttons[f"cluepanel:remove:{second}"] == "🗑 2 · upload"
    assert "cluepanel:open" in buttons

    query = await _tap(context, f"cluepanel:remove:{first}")

    assert [row.id for row in _entries(session_factory, game_id)] == [second]
    assert "1/10" in query.edit_message_text.await_args.kwargs["text"]


async def test_back_returns_to_the_preview_buttons(session_factory) -> None:
    _staged(session_factory)
    context = _context(session_factory)

    query = await _tap(context, "cluepanel:back")

    kwargs = query.edit_message_text.await_args.kwargs
    assert kwargs["text"] == "Looks good?"
    assert "preview:confirm" in _buttons(kwargs["reply_markup"])


async def test_stale_tap_after_confirm_drops_buttons(session_factory, log_records) -> None:
    game_id = _staged(session_factory)
    with session_factory() as session:
        game = session.get(Game, game_id)
        assert game is not None
        game.status = GameStatus.ACTIVE
        session.commit()
    context = _context(session_factory)

    query = await _tap(context, "cluepanel:mode:author")

    query.edit_message_reply_markup.assert_awaited_once_with(reply_markup=None)
    query.edit_message_text.assert_not_awaited()
    assert _game(session_factory, game_id).clue_screenshot_mode == ClueScreenshotMode.RANDOM
    assert any(line.level == "WARNING" for line in log_records)


async def test_tap_at_another_step_drops_buttons(session_factory, log_records) -> None:
    game_id = _staged(session_factory, setup_step=SetupStep.PICKING_METHOD)
    context = _context(session_factory)

    query = await _tap(context, "cluepanel:upload")

    query.edit_message_reply_markup.assert_awaited_once_with(reply_markup=None)
    assert _game(session_factory, game_id).setup_step is SetupStep.PICKING_METHOD
    assert any(line.level == "WARNING" for line in log_records)


async def test_each_tap_logs_info(session_factory, log_records) -> None:
    game_id = _staged(session_factory)
    [entry] = _seed_entries(session_factory, game_id, 1)
    context = _context(session_factory)
    taps = [
        "cluepanel:open",
        "cluepanel:mode:both",
        "cluepanel:upload",
        "cluepanel:remove",
        f"cluepanel:remove:{entry}",
        "cluepanel:back",
    ]

    for data in taps:
        before = len(log_records)
        await _tap(context, data)
        infos = [line for line in log_records[before:] if line.level == "INFO"]
        assert infos, data
        assert all(line.extra.get("game_id") == game_id for line in infos), data
