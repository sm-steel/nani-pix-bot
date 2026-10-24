import asyncio
from dataclasses import replace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers import item_preview
from nani_pix_bot.models.enums import CosmeticSlot, ItemSource, PreviewKind
from nani_pix_bot.models.player import Player
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import Item
from nani_pix_bot.seasons.registry import ResolvedItem
from nani_pix_bot.services import i18n
from nani_pix_bot.services.cosmetics import inventory, previews
from nani_pix_bot.services.reveal import pipeline
from tests.conftest import LogLine

FRAME = "demo_1/host_frame.border"
WIN = "demo_1/win_effect.confetti"
FLAIR = "demo_1/name_flair.blossom"
IRIS = "demo_1/host_transition.iris"
FRAME_KEY = FRAME + previews.WITH_NANI  # the cache key of a preview that shows Nani
IRIS_KEY = IRIS + previews.WITH_NANI


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


def _player(session_factory, player_id: int, username: str) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=player_id, username=username))
        session.commit()


def _resolved(item_id: str) -> ResolvedItem:
    resolved = registry.item(item_id)
    assert resolved is not None
    return resolved


def _user(user_id: int = 1, username: str | None = "amy", full_name: str = "Amy Lee"):
    user = MagicMock()
    user.id, user.username, user.full_name = user_id, username, full_name
    return user


async def _send(context: MagicMock, item_id: str, lang: str = "EN", viewer: int = 1) -> None:
    usernames = {7: "amy", 8: "bob"}
    user = _user(viewer, usernames.get(viewer, "amy"))
    await item_preview.send(
        cast(ContextTypes.DEFAULT_TYPE, context), 1, _resolved(item_id), lang, user
    )


def _remember(session_factory, item_id: str, kind: PreviewKind, file_id: str) -> None:
    with session_factory() as session:
        previews.remember(session, item_id, "EN", kind, file_id)
        session.commit()


async def test_a_still_is_rendered_sent_and_remembered(session_factory) -> None:
    context = _context(session_factory)
    await _send(context, FRAME)

    assert isinstance(context.bot.send_photo.await_args.kwargs["photo"], bytes)
    with session_factory() as session:
        row = previews.cached(session, FRAME_KEY, "EN")
        assert row is not None
        assert (row.kind, row.file_id) == (PreviewKind.PHOTO, "photo-id")


async def test_a_cached_file_id_is_reused_without_rendering(session_factory, monkeypatch) -> None:
    _remember(session_factory, FRAME_KEY, PreviewKind.PHOTO, "cached-id")

    def boom(*_args):
        raise AssertionError("must not re-render a cached preview")

    monkeypatch.setattr(previews, "render", boom)
    context = _context(session_factory)
    await _send(context, FRAME)

    assert context.bot.send_photo.await_args.kwargs["photo"] == "cached-id"


async def test_a_text_preview_is_sent_as_text_and_not_cached(session_factory) -> None:
    context = _context(session_factory)
    await _send(context, FLAIR)

    # The group's own flair format: the full name, not "@you"
    assert context.bot.send_message.await_args.kwargs["text"] == "Amy Lee 🌸"
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
        row = previews.cached(session, IRIS_KEY, "EN")
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
    _remember(session_factory, FRAME_KEY, PreviewKind.PHOTO, "stale-id")
    context = _context(session_factory)
    fresh = context.bot.send_photo.return_value
    context.bot.send_photo = AsyncMock(side_effect=[BadRequest("wrong file identifier"), fresh])
    await _send(context, FRAME)

    stale_call, fresh_call = context.bot.send_photo.await_args_list
    assert stale_call.kwargs["photo"] == "stale-id"
    assert isinstance(fresh_call.kwargs["photo"], bytes)
    with session_factory() as session:
        row = previews.cached(session, FRAME_KEY, "EN")
        assert row is not None
        assert row.file_id == "photo-id"
    assert any(r.level == "WARNING" and r.extra.get("item_id") == FRAME for r in log_records)


async def test_a_cached_preview_telegram_rejects_is_rendered_again(session_factory) -> None:
    _remember(session_factory, FRAME_KEY, PreviewKind.PHOTO, "stale")
    context = _context(session_factory)
    context.bot.send_photo = AsyncMock(
        side_effect=[BadRequest("wrong file"), MagicMock(photo=[MagicMock(file_id="fresh")])]
    )
    await _send(context, FRAME)

    with session_factory() as session:
        row = previews.cached(session, FRAME_KEY, "EN")
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
        assert previews.cached(session, FRAME_KEY, "EN") is not None
        assert previews.cached(session, FRAME_KEY, "RU") is not None


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


# --- drawn on the viewer (#427) ---


@pytest.fixture
def worker(monkeypatch) -> AsyncMock:
    submit = AsyncMock(return_value=b"mp4")
    monkeypatch.setattr(item_preview.reveal_worker, "runtime", lambda _app: object())
    monkeypatch.setattr(item_preview.reveal_worker, "submit", submit)
    return submit


@pytest.fixture
def avatar(monkeypatch) -> AsyncMock:
    fetch = AsyncMock(return_value=b"amy-avatar")
    monkeypatch.setattr(item_preview, "fetch_avatar", fetch)
    return fetch


async def test_a_win_effect_shows_the_viewers_avatar_and_name(
    session_factory, worker, avatar
) -> None:
    _player(session_factory, 7, "amy")
    await _send(_context(session_factory), WIN, viewer=7)

    badge = worker.await_args.args[-1].badge
    assert badge == pipeline.Badge(b"amy-avatar", "@amy", overlay="confetti")
    avatar.assert_awaited_once()


async def test_a_win_effect_is_kept_per_viewer(session_factory, worker, avatar) -> None:
    _player(session_factory, 7, "amy")
    _player(session_factory, 8, "bob")
    await _send(_context(session_factory), WIN, viewer=7)
    await _send(_context(session_factory), WIN, viewer=8)
    await _send(_context(session_factory), WIN, viewer=7)  # Amy's again: from the cache

    assert worker.await_count == 2
    with session_factory() as session:
        assert previews.cached(session, f"{WIN}@7{previews.WITH_NANI}", "EN") is not None
        assert previews.cached(session, f"{WIN}@8{previews.WITH_NANI}", "EN") is not None
        assert previews.cached(session, WIN, "EN") is None


async def test_a_frame_needs_no_avatar_and_is_shared(session_factory, avatar) -> None:
    await _send(_context(session_factory), FRAME, viewer=7)
    avatar.assert_not_awaited()
    with session_factory() as session:
        assert previews.cached(session, FRAME_KEY, "EN") is not None


async def test_a_banner_is_drawn_on_the_viewers_card_every_time(
    session_factory, monkeypatch, avatar
) -> None:
    _player(session_factory, 7, "amy")
    seen: list[previews.Viewer] = []

    def render(_resolved, _lang, viewer):
        seen.append(viewer)
        return previews.Preview(PreviewKind.PHOTO, image=b"png")

    monkeypatch.setattr(item_preview.previews, "render", render)
    frame = _resolved(FRAME)
    banner = replace(frame, item=replace(frame.item, slot=CosmeticSlot.PROFILE_BANNER))
    context = _context(session_factory)
    for _ in range(2):
        await item_preview.send(cast(ContextTypes.DEFAULT_TYPE, context), 7, banner, "EN", _user(7))

    assert len(seen) == 2  # never cached: their stats move on
    assert seen[0].card is not None
    assert seen[0].card.name == "@amy"
    assert seen[0].avatar == b"amy-avatar"
    with session_factory() as session:
        assert previews.cached(session, banner.item_id, "EN") is None


async def test_the_badge_names_a_player_without_username_by_full_name(
    session_factory, worker, avatar
) -> None:
    """As the real reveal does (players.badge_handle), not by the stored first name."""
    user = _user(9, username=None, full_name="Bob Smith")
    await item_preview.send(
        cast(ContextTypes.DEFAULT_TYPE, _context(session_factory)), 9, _resolved(WIN), "EN", user
    )
    assert worker.await_args.args[-1].badge.handle == "Bob Smith"


async def test_a_slow_avatar_previews_with_initials(session_factory, worker, monkeypatch) -> None:
    async def slow(*_args):
        await asyncio.sleep(1)
        return b"late"

    monkeypatch.setattr(item_preview, "fetch_avatar", slow)
    monkeypatch.setattr(item_preview, "AVATAR_TIMEOUT", 0.01)
    await _send(_context(session_factory), WIN, viewer=7)

    assert worker.await_args.args[-1].badge.avatar is None


async def test_a_viewer_that_cannot_be_drawn_says_the_preview_is_unavailable(
    session_factory, monkeypatch
) -> None:
    monkeypatch.setattr(item_preview, "fetch_avatar", AsyncMock(side_effect=RuntimeError))
    context = _context(session_factory)
    await _send(context, WIN, viewer=7)

    text = context.bot.send_message.await_args.kwargs["text"]
    assert text == i18n.t("cosmetics.preview_unavailable", "EN")


def _wear_costume(session_factory, monkeypatch, costume_art, player_id: int = 1) -> None:
    hat = Item(
        "mascot_costume.hat",
        CosmeticSlot.MASCOT_COSTUME,
        costume_art,
        {"EN": "Hat", "RU": "Hat"},
    )
    demo = registry.all_runs()["demo_1"]
    runs = {"demo_1": replace(demo, items=(*demo.items, hat))}
    monkeypatch.setattr(registry, "all_runs", lambda: runs)
    registry.all_items.cache_clear()
    with session_factory() as session:
        item_id = "demo_1/mascot_costume.hat"
        inventory.grant(session, player_id, inventory.ItemGrant(item_id, ItemSource.ADMIN))
        inventory.equip(session, player_id, item_id)
        session.commit()


async def test_send_keys_the_preview_by_the_viewers_costume(
    session_factory, costume_art, monkeypatch, log_records: list[LogLine]
) -> None:
    _wear_costume(session_factory, monkeypatch, costume_art)
    context = _context(session_factory)
    await _send(context, FRAME)

    with session_factory() as session:
        assert previews.cached(session, FRAME_KEY + "@test_hat", "EN") is not None
        assert previews.cached(session, FRAME_KEY, "EN") is None
    [line] = [r for r in log_records if r.message.startswith("previewed")]
    assert line.extra["costume"] == "test_hat"


async def test_a_plain_viewers_preview_is_unchanged(
    session_factory, log_records: list[LogLine]
) -> None:
    context = _context(session_factory)
    await _send(context, FRAME)

    with session_factory() as session:
        assert previews.cached(session, FRAME_KEY, "EN") is not None
    [line] = [r for r in log_records if r.message.startswith("previewed")]
    assert line.extra["costume"] is None
