from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers import item_preview
from nani_pix_bot.models.enums import PreviewKind
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.registry import ResolvedItem
from nani_pix_bot.services import i18n
from nani_pix_bot.services.cosmetics import previews
from nani_pix_bot.services.reveal import pipeline
from tests.conftest import LogLine

FRAME = "demo_1/host_frame.border"
FLAIR = "demo_1/name_flair.blossom"
IRIS = "demo_1/host_transition.iris"


@pytest.fixture(autouse=True)
def fake_runs(monkeypatch):
    runs = registry.discover("tests.seasons.fake_runs")
    monkeypatch.setattr(registry, "all_runs", lambda: runs)
    registry.all_items.cache_clear()
    yield runs
    registry.all_items.cache_clear()


def _context(session_factory) -> MagicMock:
    context = MagicMock()
    context.bot_data = {"session_factory": session_factory}
    sent = MagicMock()
    sent.photo = [MagicMock(file_id="small"), MagicMock(file_id="photo-id")]
    sent.video.file_id = "video-id"
    context.bot.send_photo = AsyncMock(return_value=sent)
    context.bot.send_video = AsyncMock(return_value=sent)
    context.bot.send_message = AsyncMock()
    return context


def _resolved(item_id: str) -> ResolvedItem:
    resolved = registry.item(item_id)
    assert resolved is not None
    return resolved


async def _send(context: MagicMock, item_id: str, lang: str = "EN") -> None:
    await item_preview.send(cast(ContextTypes.DEFAULT_TYPE, context), 1, _resolved(item_id), lang)


def _remember(session_factory, item_id: str, kind: PreviewKind, file_id: str) -> None:
    with session_factory() as session:
        previews.remember(session, item_id, "EN", kind, file_id)
        session.commit()


async def test_a_still_is_rendered_sent_and_remembered(session_factory) -> None:
    context = _context(session_factory)
    await _send(context, FRAME)

    assert isinstance(context.bot.send_photo.await_args.kwargs["photo"], bytes)
    with session_factory() as session:
        row = previews.cached(session, FRAME, "EN")
        assert row is not None
        assert (row.kind, row.file_id) == (PreviewKind.PHOTO, "photo-id")


async def test_a_cached_file_id_is_reused_without_rendering(session_factory, monkeypatch) -> None:
    _remember(session_factory, FRAME, PreviewKind.PHOTO, "cached-id")

    def boom(*_args):
        raise AssertionError("must not re-render a cached preview")

    monkeypatch.setattr(previews, "render", boom)
    context = _context(session_factory)
    await _send(context, FRAME)

    assert context.bot.send_photo.await_args.kwargs["photo"] == "cached-id"


async def test_a_text_preview_is_sent_as_text_and_not_cached(session_factory) -> None:
    context = _context(session_factory)
    await _send(context, FLAIR)

    assert context.bot.send_message.await_args.kwargs["text"] == "@you 🌸"
    with session_factory() as session:
        assert previews.cached(session, FLAIR, "EN") is None


async def test_a_video_without_the_worker_falls_back_to_text(session_factory) -> None:
    context = _context(session_factory)
    await _send(context, IRIS)

    context.bot.send_video.assert_not_awaited()
    text = context.bot.send_message.await_args.kwargs["text"]
    assert text == i18n.t("cosmetics.preview_unavailable", "EN")


async def test_a_video_renders_in_the_worker(session_factory, monkeypatch) -> None:
    submit = AsyncMock(return_value=b"mp4")
    monkeypatch.setattr(item_preview.reveal_worker, "runtime", lambda _app: object())
    monkeypatch.setattr(item_preview.reveal_worker, "submit", submit)
    context = _context(session_factory)
    await _send(context, IRIS)

    [call] = submit.await_args_list
    assert call.args[1] is pipeline.preview_clip
    assert context.bot.send_video.await_args.kwargs["video"] == b"mp4"
    with session_factory() as session:
        row = previews.cached(session, IRIS, "EN")
        assert row is not None
        assert (row.kind, row.file_id) == (PreviewKind.VIDEO, "video-id")


async def test_a_video_whose_worker_fails_falls_back_to_text(session_factory, monkeypatch) -> None:
    monkeypatch.setattr(item_preview.reveal_worker, "runtime", lambda _app: object())
    monkeypatch.setattr(
        item_preview.reveal_worker, "submit", AsyncMock(side_effect=RuntimeError("dead"))
    )
    context = _context(session_factory)
    await _send(context, IRIS)

    text = context.bot.send_message.await_args.kwargs["text"]
    assert text == i18n.t("cosmetics.preview_unavailable", "EN")


async def test_a_still_that_fails_to_render_says_so(
    session_factory, monkeypatch, log_records: list[LogLine]
) -> None:
    def broken(*_args):
        raise OSError("art missing")

    monkeypatch.setattr(previews, "render", broken)
    context = _context(session_factory)
    await _send(context, FRAME)

    text = context.bot.send_message.await_args.kwargs["text"]
    assert text == i18n.t("cosmetics.preview_unavailable", "EN")
    assert any(r.level == "ERROR" for r in log_records)


async def test_a_transition_whose_look_is_gone_falls_back_to_text(
    session_factory, monkeypatch, log_records: list[LogLine]
) -> None:
    monkeypatch.setattr(previews, "RevealEffect", {})  # RevealEffect[look] -> KeyError
    context = _context(session_factory)
    await _send(context, IRIS)

    text = context.bot.send_message.await_args.kwargs["text"]
    assert text == i18n.t("cosmetics.preview_unavailable", "EN")
    assert any(r.level == "ERROR" for r in log_records)


async def test_a_rejected_cached_file_id_is_dropped_and_the_preview_re_rendered(
    session_factory, log_records: list[LogLine]
) -> None:
    _remember(session_factory, FRAME, PreviewKind.PHOTO, "stale-id")
    context = _context(session_factory)
    fresh = context.bot.send_photo.return_value
    context.bot.send_photo = AsyncMock(side_effect=[BadRequest("wrong file identifier"), fresh])
    await _send(context, FRAME)

    stale_call, fresh_call = context.bot.send_photo.await_args_list
    assert stale_call.kwargs["photo"] == "stale-id"
    assert isinstance(fresh_call.kwargs["photo"], bytes)
    with session_factory() as session:
        row = previews.cached(session, FRAME, "EN")
        assert row is not None
        assert row.file_id == "photo-id"
    assert any(r.level == "WARNING" and r.extra.get("item_id") == FRAME for r in log_records)


async def test_a_cached_preview_telegram_rejects_is_rendered_again(session_factory) -> None:
    _remember(session_factory, FRAME, PreviewKind.PHOTO, "stale")
    context = _context(session_factory)
    context.bot.send_photo = AsyncMock(
        side_effect=[BadRequest("wrong file"), MagicMock(photo=[MagicMock(file_id="fresh")])]
    )
    await _send(context, FRAME)

    with session_factory() as session:
        row = previews.cached(session, FRAME, "EN")
        assert row is not None
        assert row.file_id == "fresh"


async def test_each_language_gets_its_own_cached_preview(session_factory) -> None:
    context = _context(session_factory)
    await _send(context, FRAME, "EN")
    await _send(context, FRAME, "RU")

    assert [c.kwargs["photo"].__class__ for c in context.bot.send_photo.await_args_list] == [
        bytes,
        bytes,
    ]
    with session_factory() as session:
        assert previews.cached(session, FRAME, "EN") is not None
        assert previews.cached(session, FRAME, "RU") is not None


async def test_a_telegram_error_is_logged_not_raised(
    session_factory, log_records: list[LogLine]
) -> None:
    context = _context(session_factory)
    context.bot.send_photo = AsyncMock(side_effect=BadRequest("nope"))
    await _send(context, FRAME)

    assert any(r.level == "ERROR" for r in log_records)


async def _send_image(context: MagicMock, render, caption: str | None = None) -> None:
    picture = item_preview.Picture("consumable/clue_token", render, caption)
    await item_preview.send_image(cast(ContextTypes.DEFAULT_TYPE, context), 1, picture, "EN")


async def test_an_image_is_rendered_sent_with_its_caption_and_remembered(session_factory) -> None:
    context = _context(session_factory)
    await _send_image(context, lambda: b"png", "a note")

    call = context.bot.send_photo.await_args
    assert (call.kwargs["photo"], call.kwargs["caption"]) == (b"png", "a note")
    with session_factory() as session:
        row = previews.cached(session, "consumable/clue_token", "EN")
        assert row is not None
        assert (row.kind, row.file_id) == (PreviewKind.PHOTO, "photo-id")


async def test_a_cached_image_is_sent_by_file_id_without_rendering(session_factory) -> None:
    _remember(session_factory, "consumable/clue_token", PreviewKind.PHOTO, "cached-id")

    def boom() -> bytes:
        raise AssertionError("must not re-render a cached image")

    context = _context(session_factory)
    await _send_image(context, boom, "a note")

    call = context.bot.send_photo.await_args
    assert (call.kwargs["photo"], call.kwargs["caption"]) == ("cached-id", "a note")


async def test_a_rejected_cached_image_is_dropped_and_drawn_again(session_factory) -> None:
    _remember(session_factory, "consumable/clue_token", PreviewKind.PHOTO, "stale")
    context = _context(session_factory)
    fresh = context.bot.send_photo.return_value
    context.bot.send_photo = AsyncMock(side_effect=[BadRequest("wrong file"), fresh])
    await _send_image(context, lambda: b"png")

    assert context.bot.send_photo.await_args_list[-1].kwargs["photo"] == b"png"
    with session_factory() as session:
        row = previews.cached(session, "consumable/clue_token", "EN")
        assert row is not None
        assert row.file_id == "photo-id"


async def test_an_image_that_fails_to_send_is_logged_not_raised(
    session_factory, log_records: list[LogLine]
) -> None:
    context = _context(session_factory)
    context.bot.send_photo = AsyncMock(side_effect=BadRequest("nope"))
    await _send_image(context, lambda: b"png")

    assert any(r.level == "ERROR" for r in log_records)


async def test_an_image_that_fails_to_draw_falls_back_to_text(
    session_factory, log_records: list[LogLine]
) -> None:
    def broken() -> bytes:
        raise ValueError("bad icon")

    context = _context(session_factory)
    await _send_image(context, broken, "a note")

    context.bot.send_photo.assert_not_awaited()
    context.bot.send_message.assert_awaited_once_with(
        chat_id=1, text=i18n.t("cosmetics.preview_unavailable", "EN")
    )
    assert any(r.level == "ERROR" and "consumable/clue_token" in r.message for r in log_records)
    with session_factory() as session:
        assert previews.cached(session, "consumable/clue_token", "EN") is None
