from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import InputMediaPhoto, InputMediaVideo
from telegram.error import TelegramError, TimedOut

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import reveal
from nani_pix_bot.jobs.timers import current_image
from nani_pix_bot.jobs.timers.current_image import RevealTarget, RevealWinner
from nani_pix_bot.models.enums import RevealEffect
from nani_pix_bot.services.reveal import store


def _context(session_factory) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": -100,
        "game_topic_id": 5,
        "reveal_cache": reveal.RevealCache(),
    }
    context.bot.send_video = AsyncMock(return_value=MagicMock(message_id=11))
    context.bot.send_photo = AsyncMock(return_value=MagicMock(message_id=12))
    context.bot.send_media_group = AsyncMock(
        return_value=[MagicMock(message_id=13), MagicMock(message_id=14)]
    )
    context.bot.pin_chat_message = AsyncMock()
    context.bot.unpin_chat_message = AsyncMock()
    return context


async def test_video_sent_with_caption_and_slot_finished(session_factory, monkeypatch) -> None:
    render = AsyncMock(return_value=b"mp4")
    finished = MagicMock()
    monkeypatch.setattr(reveal, "render_reveal", render)
    monkeypatch.setattr(reveal, "finished", finished)
    context = _context(session_factory)
    sent = await current_image.post_reveal(
        context,
        session_factory,
        target=RevealTarget(3, RevealWinner(9, "@w")),
        photo=b"png",
        caption="cap",
    )
    assert sent is not None
    assert sent.message_id == 11
    kwargs = context.bot.send_video.await_args.kwargs
    assert (kwargs["video"], kwargs["caption"], kwargs["message_thread_id"]) == (b"mp4", "cap", 5)
    assert render.await_args is not None
    assert render.await_args.args[1:] == (3, b"png", 9, "@w")
    context.bot.send_photo.assert_not_awaited()
    context.bot.pin_chat_message.assert_awaited_once()
    finished.assert_called_once_with(context.application, 3)


async def test_unsolved_reveal_passes_no_winner(session_factory, monkeypatch) -> None:
    render = AsyncMock(return_value=b"mp4")
    monkeypatch.setattr(reveal, "render_reveal", render)
    monkeypatch.setattr(reveal, "finished", MagicMock())
    await current_image.post_reveal(
        _context(session_factory),
        session_factory,
        target=RevealTarget(3, None),
        photo=b"png",
        caption="c",
    )
    assert render.await_args is not None
    assert render.await_args.args[3:] == (None, None)


async def test_no_video_falls_back_to_photo(session_factory, monkeypatch) -> None:
    monkeypatch.setattr(reveal, "render_reveal", AsyncMock(return_value=None))
    finished = MagicMock()
    monkeypatch.setattr(reveal, "finished", finished)
    context = _context(session_factory)
    sent = await current_image.post_reveal(
        context, session_factory, target=RevealTarget(3, None), photo=b"png", caption="cap"
    )
    assert sent is not None
    assert sent.message_id == 12
    context.bot.send_video.assert_not_awaited()
    assert context.bot.send_photo.await_args.kwargs["photo"] == b"png"
    finished.assert_called_once_with(context.application, 3)


async def test_telegram_rejecting_video_falls_back_to_photo(session_factory, monkeypatch) -> None:
    monkeypatch.setattr(reveal, "render_reveal", AsyncMock(return_value=b"mp4"))
    monkeypatch.setattr(reveal, "finished", MagicMock())
    context = _context(session_factory)
    context.bot.send_video = AsyncMock(side_effect=TelegramError("bad video"))
    sent = await current_image.post_reveal(
        context, session_factory, target=RevealTarget(3, None), photo=b"png", caption="cap"
    )
    assert sent is not None
    assert sent.message_id == 12


async def test_nothing_sent_keeps_the_slot(session_factory, monkeypatch) -> None:
    monkeypatch.setattr(reveal, "render_reveal", AsyncMock(return_value=None))
    finished = MagicMock()
    monkeypatch.setattr(reveal, "finished", finished)
    context = _context(session_factory)
    context.bot.send_photo = AsyncMock(side_effect=TelegramError("down"))
    assert (
        await current_image.post_reveal(
            context, session_factory, target=RevealTarget(3, None), photo=b"png", caption="cap"
        )
        is None
    )
    finished.assert_not_called()


@pytest.mark.parametrize(("choice", "video_index"), [("a", 0), ("b", 1)])
async def test_pair_puts_the_chosen_image_first_as_video(
    session_factory, monkeypatch, choice, video_index
) -> None:
    with session_scope(session_factory) as session:
        store.reserve(session, 3, RevealEffect.IRIS, choice)
    render = AsyncMock(return_value=b"mp4")
    monkeypatch.setattr(reveal, "render_reveal", render)
    monkeypatch.setattr(reveal, "finished", MagicMock())
    context = _context(session_factory)
    photos = (b"img-a", b"img-b")
    sent = await current_image.post_reveal_pair(
        context, session_factory, target=RevealTarget(3, None), photos=photos, caption="cap"
    )
    assert sent is not None
    assert [message.message_id for message in sent] == [13, 14]
    media = context.bot.send_media_group.await_args.kwargs["media"]
    assert isinstance(media[0], InputMediaVideo)
    assert media[0].caption == "cap"
    assert isinstance(media[1], InputMediaPhoto)
    assert media[1].caption is None
    assert render.await_args is not None
    assert render.await_args.args[2] == photos[video_index]


async def test_pair_falls_back_to_the_photo_album(session_factory, monkeypatch) -> None:
    monkeypatch.setattr(reveal, "render_reveal", AsyncMock(return_value=None))
    monkeypatch.setattr(reveal, "finished", MagicMock())
    context = _context(session_factory)
    await current_image.post_reveal_pair(
        context, session_factory, target=RevealTarget(3, None), photos=(b"a", b"b"), caption="cap"
    )
    media = context.bot.send_media_group.await_args.kwargs["media"]
    assert all(isinstance(item, InputMediaPhoto) for item in media)
    assert media[0].caption == "cap"


async def test_video_timeout_does_not_fall_back(session_factory, monkeypatch) -> None:
    monkeypatch.setattr(reveal, "render_reveal", AsyncMock(return_value=b"mp4"))
    finished = MagicMock()
    monkeypatch.setattr(reveal, "finished", finished)
    context = _context(session_factory)
    context.bot.send_video = AsyncMock(side_effect=TimedOut())
    sent = await current_image.post_reveal(
        context, session_factory, target=RevealTarget(3, None), photo=b"png", caption="cap"
    )
    assert sent is None
    context.bot.send_photo.assert_not_awaited()
    finished.assert_not_called()


async def test_video_album_timeout_does_not_fall_back(session_factory, monkeypatch) -> None:
    monkeypatch.setattr(reveal, "render_reveal", AsyncMock(return_value=b"mp4"))
    finished = MagicMock()
    monkeypatch.setattr(reveal, "finished", finished)
    context = _context(session_factory)
    context.bot.send_media_group = AsyncMock(side_effect=TimedOut())
    sent = await current_image.post_reveal_pair(
        context,
        session_factory,
        target=RevealTarget(3, None),
        photos=(b"a", b"b"),
        caption="cap",
    )
    assert sent is None
    assert context.bot.send_media_group.await_count == 1
    finished.assert_not_called()


async def test_finished_failure_never_masks_the_send(
    session_factory, monkeypatch, log_records
) -> None:
    monkeypatch.setattr(reveal, "render_reveal", AsyncMock(return_value=b"mp4"))
    monkeypatch.setattr(reveal, "finished", MagicMock(side_effect=RuntimeError("db down")))
    sent = await current_image.post_reveal(
        _context(session_factory),
        session_factory,
        target=RevealTarget(3, None),
        photo=b"png",
        caption="cap",
    )
    assert sent is not None
    assert [line.level for line in log_records if line.level == "ERROR"] == ["ERROR"]


async def test_a_rejected_video_album_falls_back_to_the_photo_album(
    session_factory, monkeypatch
) -> None:
    monkeypatch.setattr(reveal, "render_reveal", AsyncMock(return_value=b"mp4"))
    finished = MagicMock()
    monkeypatch.setattr(reveal, "finished", finished)
    context = _context(session_factory)
    context.bot.send_media_group = AsyncMock(
        side_effect=[
            TelegramError("bad video"),
            [MagicMock(message_id=13), MagicMock(message_id=14)],
        ]
    )
    sent = await current_image.post_reveal_pair(
        context, session_factory, target=RevealTarget(3, None), photos=(b"a", b"b"), caption="cap"
    )
    assert sent is not None
    assert [message.message_id for message in sent] == [13, 14]
    first, second = (call.kwargs["media"] for call in context.bot.send_media_group.await_args_list)
    assert isinstance(first[0], InputMediaVideo)
    assert all(isinstance(item, InputMediaPhoto) for item in second)
    assert second[0].caption == "cap"
    finished.assert_called_once_with(context.application, 3)


async def test_a_pair_with_nothing_sent_keeps_the_slot(session_factory, monkeypatch) -> None:
    monkeypatch.setattr(reveal, "render_reveal", AsyncMock(return_value=b"mp4"))
    finished = MagicMock()
    monkeypatch.setattr(reveal, "finished", finished)
    context = _context(session_factory)
    context.bot.send_media_group = AsyncMock(side_effect=TelegramError("down"))
    sent = await current_image.post_reveal_pair(
        context, session_factory, target=RevealTarget(3, None), photos=(b"a", b"b"), caption="cap"
    )
    assert sent is None
    assert context.bot.send_media_group.await_count == 2  # the video album, then the photo album
    finished.assert_not_called()


@pytest.mark.parametrize("slot_game_id", [None, 99])
async def test_a_missing_or_foreign_slot_renders_image_a(
    session_factory, monkeypatch, slot_game_id
) -> None:
    if slot_game_id is not None:
        with session_scope(session_factory) as session:
            store.reserve(session, slot_game_id, RevealEffect.IRIS, "b")
    render = AsyncMock(return_value=None)
    monkeypatch.setattr(reveal, "render_reveal", render)
    monkeypatch.setattr(reveal, "finished", MagicMock())
    await current_image.post_reveal_pair(
        _context(session_factory),
        session_factory,
        target=RevealTarget(3, None),
        photos=(b"img-a", b"img-b"),
        caption="cap",
    )
    assert render.await_args is not None
    assert render.await_args.args[2] == b"img-a"


async def test_the_reveal_album_pins_its_first_message(session_factory, monkeypatch) -> None:
    monkeypatch.setattr(reveal, "render_reveal", AsyncMock(return_value=b"mp4"))
    monkeypatch.setattr(reveal, "finished", MagicMock())
    context = _context(session_factory)
    await current_image.post_reveal_pair(
        context, session_factory, target=RevealTarget(3, None), photos=(b"a", b"b"), caption="cap"
    )
    context.bot.pin_chat_message.assert_awaited_once()
    assert context.bot.pin_chat_message.await_args.kwargs["message_id"] == 13
