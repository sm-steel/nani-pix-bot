import io
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image
from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.error import TimedOut
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start import intake, preview
from nani_pix_bot.commands.dm_start.keyboards import (
    ANILIST_METHOD_CALLBACK_DATA,
    PREVIEW_CONFIRM_CALLBACK_DATA,
    SHIKIMORI_METHOD_CALLBACK_DATA,
)
from nani_pix_bot.jobs import timers as timeout_module
from nani_pix_bot.models.bot_settings import BotSettings
from nani_pix_bot.models.enums import DEFAULT_ALGORITHM, GameStatus, Provider, SetupStep
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.turn_state import TurnState
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n
from nani_pix_bot.services import pixelate as pixelate_service
from nani_pix_bot.services.search.anilist import AniListResult
from nani_pix_bot.services.settings import stage_config

_FRIEREN = AniListResult(
    anilist_id=99,
    title_romaji="Sousou no Frieren",
    title_english="Frieren: Beyond Journey's End",
    title_native="葬送のフリーレン",
    synonyms=["Frieren"],
    year=2023,
)


def _make_update(
    *,
    user_id: int = 1,
    username: str = "player",
    full_name: str = "Starter Name",
    photo_file_id: str | None = None,
) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.username = username
    update.effective_user.full_name = full_name
    update.effective_chat.type = "private"
    update.message.reply_text = AsyncMock()
    update.message.photo = [MagicMock(file_id=photo_file_id)] if photo_file_id else []
    return update


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
    # photo_handler downloads the photo's bytes immediately, once, rather
    # than storing the Telegram file_id — see models/game.py's
    # original_image docstring.
    context.bot.get_file = AsyncMock()
    context.bot.get_file.return_value.download_as_bytearray = AsyncMock(
        return_value=bytearray(b"downloaded-bytes")
    )
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


def _set_language(session_factory, language: str) -> None:
    with session_factory() as session:
        session.add(BotSettings(id=1, language=language))
        session.commit()


def _staged_setup_game(session_factory, *, starter_id: int = 1) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=starter_id))
        session.commit()
        game = game_service.create_setup_game(
            session, starter_id=starter_id, original_image=b"file123"
        )
        game.source = Provider.ANILIST
        game_service.stage_result(game, _FRIEREN, source=Provider.ANILIST)
        game.setup_step = SetupStep.CONFIRMING
        session.commit()


async def test_photo_handler_creates_a_setup_game_when_turn_is_open(session_factory) -> None:
    update = _make_update(user_id=1, photo_file_id="file123")
    context = _make_context(session_factory)

    await intake.photo_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    with session_factory() as session:
        games = session.query(Game).all()
        assert len(games) == 1
        assert games[0].status == GameStatus.SETUP
        assert games[0].original_image == b"downloaded-bytes"
        assert games[0].starter_id == 1
    update.message.reply_text.assert_awaited_once()


async def test_photo_handler_notifies_the_group_that_setup_started(session_factory) -> None:
    update = _make_update(user_id=1, photo_file_id="file123", full_name="Starter Name")
    context = _make_context(session_factory)

    await intake.photo_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_message.assert_awaited_once()
    _, kwargs = context.bot.send_message.await_args
    assert kwargs["chat_id"] == 555
    assert "Starter Name" in kwargs["text"]
    with session_factory() as session:
        game = session.query(Game).filter_by(starter_id=1).one()
    assert f"#{game.id}" in kwargs["text"]


async def test_photo_handler_schedules_the_setup_abandon_timer(session_factory) -> None:
    update = _make_update(user_id=1, photo_file_id="file123")
    context = _make_context(session_factory)

    await intake.photo_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    with session_factory() as session:
        game = session.query(Game).filter_by(starter_id=1).one()
    names = [call.kwargs["name"] for call in context.job_queue.run_once.call_args_list]
    assert timeout_module.setup_abandon_job_name(game.id) in names


async def test_photo_handler_cancels_turn_timers_when_the_designated_starter_begins(
    session_factory,
) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.add(TurnState(id=1, next_starter_id=1))
        session.commit()

    update = _make_update(user_id=1, photo_file_id="file123")
    context = _make_context(session_factory)

    await intake.photo_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    assert context.job_queue.get_jobs_by_name.call_count >= 2


async def test_photo_handler_cancels_pending_idle_autostart(session_factory) -> None:
    """A human successfully starting a game must cancel any pending
    idle-autostart timer, so "nobody started a game for 24h" stays
    literally true regardless of who starts one next (issue #159)."""
    update = _make_update(user_id=1, photo_file_id="file123")
    context = _make_context(session_factory)

    await intake.photo_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    names = [call.args[0] for call in context.job_queue.get_jobs_by_name.call_args_list]
    assert timeout_module.IDLE_AUTOSTART_JOB_NAME in names


async def test_photo_handler_shows_the_method_selection_keyboard(session_factory) -> None:
    update = _make_update(user_id=1, photo_file_id="file123")
    context = _make_context(session_factory)

    await intake.photo_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    _, kwargs = update.message.reply_text.await_args
    callbacks = [
        button.callback_data for row in kwargs["reply_markup"].inline_keyboard for button in row
    ]
    assert callbacks[:2] == [ANILIST_METHOD_CALLBACK_DATA, SHIKIMORI_METHOD_CALLBACK_DATA]


def _real_png() -> bytes:
    image = Image.new("RGB", (64, 64))
    pixels = image.load()
    assert pixels is not None
    for x in range(64):
        for y in range(64):
            pixels[x, y] = (x * 4, y * 4, (x + y) * 2)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _uploading(context: MagicMock, image_bytes: bytes) -> None:
    context.bot.get_file.return_value.download_as_bytearray = AsyncMock(
        return_value=bytearray(image_bytes)
    )


async def test_photo_handler_shows_the_first_stage_before_the_method_selection(
    session_factory,
) -> None:
    """Issue #201: the starter sees how their screenshot will look at
    stage 1 before spending any effort identifying the anime — not only
    in the confirmation album at the very end."""
    original = _real_png()
    update = _make_update(user_id=1, photo_file_id="file123")
    calls = MagicMock()
    update.message.reply_photo = AsyncMock()
    calls.attach_mock(update.message.reply_photo, "reply_photo")
    calls.attach_mock(update.message.reply_text, "reply_text")
    context = _make_context(session_factory)
    _uploading(context, original)

    await intake.photo_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    with session_factory() as session:
        first_stage = stage_config.get_stage_config(session, game_service.STAGE_ORDER[0])
    expected = pixelate_service.pixelate(original, first_stage.target_width, DEFAULT_ALGORITHM)
    photo_call = update.message.reply_photo.await_args
    assert photo_call is not None
    assert photo_call.kwargs["photo"] == expected
    assert photo_call.kwargs["caption"] == i18n.t("dm_start.first_stage_preview", "en")
    assert [name for name, _, _ in calls.mock_calls] == ["reply_photo", "reply_text"]


async def test_photo_handler_still_offers_the_methods_when_the_image_cannot_be_pixelated(
    session_factory,
) -> None:
    """The preview is a convenience: an image Pillow can't read must not
    strand the starter before the method selection."""
    update = _make_update(user_id=1, photo_file_id="file123")
    update.message.reply_photo = AsyncMock()
    context = _make_context(session_factory)
    _uploading(context, b"not an image")

    await intake.photo_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    update.message.reply_photo.assert_not_awaited()
    assert "reply_markup" in update.message.reply_text.await_args.kwargs


async def test_photo_handler_still_offers_the_methods_when_the_preview_send_fails(
    session_factory,
) -> None:
    update = _make_update(user_id=1, photo_file_id="file123")
    update.message.reply_photo = AsyncMock(side_effect=TimedOut())
    context = _make_context(session_factory)
    _uploading(context, _real_png())

    await intake.photo_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    update.message.reply_photo.assert_awaited_once()
    assert "reply_markup" in update.message.reply_text.await_args.kwargs


async def test_photo_handler_prefers_shikimori_first_when_language_is_ru(session_factory) -> None:
    _set_language(session_factory, "RU")
    update = _make_update(user_id=1, photo_file_id="file123")
    context = _make_context(session_factory)

    await intake.photo_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    _, kwargs = update.message.reply_text.await_args
    callbacks = [
        button.callback_data for row in kwargs["reply_markup"].inline_keyboard for button in row
    ]
    assert callbacks[:2] == [SHIKIMORI_METHOD_CALLBACK_DATA, ANILIST_METHOD_CALLBACK_DATA]


async def test_photo_handler_rejects_when_it_is_not_their_turn(session_factory) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=2))
        session.add(TurnState(id=1, next_starter_id=2))
        session.commit()

    update = _make_update(user_id=1, photo_file_id="file123")
    context = _make_context(session_factory)

    await intake.photo_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    with session_factory() as session:
        assert session.query(Game).count() == 0
    update.message.reply_text.assert_awaited_once()
    reply_text = update.message.reply_text.await_args.args[0]
    assert "turn" in reply_text.lower()


async def test_photo_handler_rejects_when_games_are_disabled(session_factory) -> None:
    with session_factory() as session:
        session.add(BotSettings(id=1, games_enabled=False))
        session.commit()

    update = _make_update(user_id=1, photo_file_id="file123")
    context = _make_context(session_factory)

    await intake.photo_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    with session_factory() as session:
        assert session.query(Game).count() == 0
    update.message.reply_text.assert_awaited_once()
    reply_text = update.message.reply_text.await_args.args[0]
    assert "disabled" in reply_text.lower() or "paused" in reply_text.lower()


async def test_photo_handler_ignores_non_photo_messages(session_factory) -> None:
    update = _make_update(user_id=1, photo_file_id=None)
    context = _make_context(session_factory)

    await intake.photo_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    with session_factory() as session:
        assert session.query(Game).count() == 0
    update.message.reply_text.assert_not_awaited()


async def test_photo_handler_rejects_non_group_members(session_factory) -> None:
    update = _make_update(user_id=1, photo_file_id="file123")
    context = _make_context(session_factory)
    context.bot.get_chat_member = AsyncMock(return_value=MagicMock(status=ChatMemberStatus.LEFT))

    await intake.photo_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    with session_factory() as session:
        assert session.query(Game).count() == 0
    update.message.reply_text.assert_awaited_once()
    reply_text = update.message.reply_text.await_args.args[0]
    assert "member" in reply_text.lower()


async def test_photo_handler_updates_the_image_and_reshows_the_preview_when_changing(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(preview.pixelate_service, "pixelate", lambda *_: b"pixelated")
    _staged_setup_game(session_factory)
    with session_factory() as session:
        game = session.query(Game).filter_by(starter_id=1).one()
        game.setup_step = SetupStep.AWAITING_PHOTO_CHANGE
        session.commit()

    update = _make_update(user_id=1, photo_file_id="new-file-456")
    context = _make_callback_context(session_factory)

    await intake.photo_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    with session_factory() as session:
        assert session.query(Game).count() == 1  # no duplicate game created
        fetched = session.query(Game).filter_by(starter_id=1).one()
        assert fetched.original_image == b"original-bytes"
        assert fetched.setup_step == SetupStep.CONFIRMING

    context.bot.get_file.assert_awaited_once_with("new-file-456")
    context.bot.send_media_group.assert_awaited_once()
    _, kwargs = context.bot.send_media_group.await_args
    assert kwargs["chat_id"] == 1


async def test_photo_handler_keeps_the_new_image_when_the_preview_album_times_out(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(preview.pixelate_service, "pixelate", lambda *_: b"pixelated")
    _staged_setup_game(session_factory)
    with session_factory() as session:
        game = session.query(Game).filter_by(starter_id=1).one()
        game.setup_step = SetupStep.AWAITING_PHOTO_CHANGE
        session.commit()

    update = _make_update(user_id=1, photo_file_id="new-file-456")
    context = _make_callback_context(session_factory)
    context.bot.send_media_group = AsyncMock(side_effect=TimedOut())

    with pytest.raises(TimedOut):
        await intake.photo_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    with session_factory() as session:
        fetched = session.query(Game).filter_by(starter_id=1).one()
        assert fetched.original_image == b"original-bytes"
        assert fetched.setup_step == SetupStep.CONFIRMING


async def test_photo_handler_accepts_an_upload_while_picking_a_screenshot(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sending a screenshot while the source menu is up — including the
    one a provider failure drops you back onto — is an obvious way to
    say "use this one". It used to fall through to _start_new_game and
    come back as "it's not your turn", which made no sense: it's the
    starter's own setup."""
    monkeypatch.setattr(preview.pixelate_service, "pixelate", lambda *_: b"pixelated")
    _staged_setup_game(session_factory)
    with session_factory() as session:
        game = session.query(Game).filter_by(starter_id=1).one()
        game.setup_step = SetupStep.PICKING_SCREENSHOT
        session.commit()

    update = _make_update(user_id=1, photo_file_id="own-screenshot")
    context = _make_callback_context(session_factory)

    await intake.photo_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    with session_factory() as session:
        assert session.query(Game).count() == 1  # no duplicate game created
        fetched = session.query(Game).filter_by(starter_id=1).one()
        assert fetched.original_image == b"original-bytes"
        assert fetched.setup_step == SetupStep.CONFIRMING
    context.bot.send_media_group.assert_awaited_once()  # the preview album
    update.message.reply_text.assert_not_awaited()  # no "not your turn"


async def test_photo_handler_reshows_the_preview_on_a_step_that_takes_no_photo(
    session_factory,
) -> None:
    """A photo at CONFIRMING isn't a replacement (that's what "Change
    image" is for), so it falls through to _start_new_game — which used
    to answer the starter's own setup with "it's not your turn". The
    same non-sequitur as the PICKING_SCREENSHOT case above, from the
    other side: here the answer is the screen they're on, not the
    photo."""
    _staged_setup_game(session_factory)  # parked on CONFIRMING

    update = _make_update(user_id=1, photo_file_id="unexpected-photo")
    context = _make_context(session_factory)

    await intake.photo_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    with session_factory() as session:
        assert session.query(Game).count() == 1
        fetched = session.query(Game).filter_by(starter_id=1).one()
        assert fetched.setup_step == SetupStep.CONFIRMING
        assert fetched.original_image == b"file123"  # the staged image is kept
    reply_text = update.message.reply_text.await_args.args[0]
    assert "turn" not in reply_text.lower()
    _, kwargs = update.message.reply_text.await_args
    callbacks = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert PREVIEW_CONFIRM_CALLBACK_DATA in callbacks


async def test_photo_handler_upload_keeps_the_identification_provider_id(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: identify on Shikimori, tap Shikimori for screenshots,
    then upload your own photo instead. The tap only moves the *picker*
    onto Shikimori — it stages no image — so there is no screenshot
    selection to clear, and shikimori_id (kept so a later cross-search
    can reuse it, see MECHANICS.md's "Starting a game") must survive.
    While one column carried both meanings, this upload deleted it."""
    monkeypatch.setattr(preview.pixelate_service, "pixelate", lambda *_: b"pixelated")
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.commit()
        game = game_service.create_setup_game(session, starter_id=1)
        game.source = Provider.SHIKIMORI
        game.shikimori_id = 52991
        game.title_english = "Frieren: Beyond Journey's End"
        game.setup_step = SetupStep.PICKING_SCREENSHOT
        game.screenshot_picker_provider = Provider.SHIKIMORI
        session.commit()

    update = _make_update(user_id=1, photo_file_id="own-screenshot")
    context = _make_callback_context(session_factory)

    await intake.photo_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    with session_factory() as session:
        fetched = session.query(Game).filter_by(starter_id=1).one()
        assert fetched.shikimori_id == 52991
        assert fetched.original_image == b"original-bytes"
        # A genuine upload — nothing API-sourced backs it — and the
        # picker is abandoned, so a stray typed message can't be read as
        # a screenshot query afterwards.
        assert fetched.screenshot_source is None
        assert fetched.screenshot_picker_provider is None
        assert fetched.setup_step == SetupStep.CONFIRMING
