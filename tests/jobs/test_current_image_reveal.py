from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import TelegramError, TimedOut

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import reveal
from nani_pix_bot.jobs.reveal import RevealWinner
from nani_pix_bot.jobs.timers import current_image
from nani_pix_bot.jobs.timers.current_image import RevealTarget
from nani_pix_bot.models import ClueScreenshotHandout
from nani_pix_bot.models.enums import RevealEffect
from nani_pix_bot.services.clues import handouts
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
    assert render.await_args.args[1:] == (3, b"png", RevealWinner(9, "@w"))
    context.bot.send_photo.assert_not_awaited()
    context.bot.pin_chat_message.assert_awaited_once()
    finished.assert_called_once_with(context.application, 3)


async def test_the_winners_overlay_reaches_render_reveal(session_factory, monkeypatch) -> None:
    render = AsyncMock(return_value=b"mp4")
    monkeypatch.setattr(reveal, "render_reveal", render)
    monkeypatch.setattr(reveal, "finished", MagicMock())
    await current_image.post_reveal(
        _context(session_factory),
        session_factory,
        target=RevealTarget(3, RevealWinner(9, "@w", overlay="confetti")),
        photo=b"png",
        caption="cap",
    )
    assert render.await_args is not None
    assert render.await_args.args[3] == RevealWinner(9, "@w", overlay="confetti")


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
    assert render.await_args.args[3:] == (None,)


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


def _add_clues(session_factory, game_id: int, images: list[bytes]) -> None:
    with session_scope(session_factory) as session:
        for number, image in enumerate(images):
            session.add(
                ClueScreenshotHandout(
                    game_id=game_id, screenshot_url=f"https://x/{number}.jpg", image=image
                )
            )


def _kept(session_factory, game_id: int) -> list[bytes]:
    with session_scope(session_factory) as session:
        return handouts.revealed_images(session, game_id)


def _video_ok(monkeypatch) -> MagicMock:
    monkeypatch.setattr(reveal, "render_reveal", AsyncMock(return_value=b"mp4"))
    finished = MagicMock()
    monkeypatch.setattr(reveal, "finished", finished)
    return finished


async def _reveal(context, session_factory, **kwargs):
    return await current_image.post_reveal(
        context,
        session_factory,
        target=RevealTarget(3, None, **kwargs),
        photo=b"png",
        caption="cap",
    )


async def test_reveal_then_clue_album(session_factory, monkeypatch) -> None:
    _video_ok(monkeypatch)
    _add_clues(session_factory, 3, [b"c1", b"c2"])
    context = _context(session_factory)
    sent = await _reveal(context, session_factory)
    assert sent is not None
    assert sent.message_id == 11
    context.bot.send_video.assert_awaited_once()
    context.bot.send_photo.assert_not_awaited()
    media = context.bot.send_media_group.await_args.kwargs["media"]
    assert [item.media.input_file_content for item in media] == [b"c1", b"c2"]
    assert media[0].caption
    assert media[1].caption is None
    context.bot.pin_chat_message.assert_awaited_once()  # the video only


async def test_reveal_single_clue_is_photo(session_factory, monkeypatch) -> None:
    _video_ok(monkeypatch)
    _add_clues(session_factory, 3, [b"c1"])
    context = _context(session_factory)
    await _reveal(context, session_factory)
    kwargs = context.bot.send_photo.await_args.kwargs
    assert (kwargs["photo"], kwargs["message_thread_id"]) == (b"c1", 5)
    assert kwargs["caption"]
    context.bot.send_media_group.assert_not_awaited()
    context.bot.pin_chat_message.assert_awaited_once()


async def test_reveal_no_clues_no_second_message(session_factory, monkeypatch) -> None:
    _video_ok(monkeypatch)
    context = _context(session_factory)
    await _reveal(context, session_factory)
    context.bot.send_photo.assert_not_awaited()
    context.bot.send_media_group.assert_not_awaited()


async def test_reveal_eleven_clues_two_albums(session_factory, monkeypatch) -> None:
    _video_ok(monkeypatch)
    _add_clues(session_factory, 3, [bytes([n]) for n in range(11)])
    context = _context(session_factory)
    await _reveal(context, session_factory)
    [album] = context.bot.send_media_group.await_args_list
    assert len(album.kwargs["media"]) == 10
    assert album.kwargs["media"][0].caption
    assert context.bot.send_photo.await_count == 1  # the lone eleventh
    assert not context.bot.send_photo.await_args.kwargs["caption"]


async def test_timed_out_video_still_posts_extras(session_factory, monkeypatch) -> None:
    finished = _video_ok(monkeypatch)
    _add_clues(session_factory, 3, [b"c1", b"c2"])
    context = _context(session_factory)
    context.bot.send_video = AsyncMock(side_effect=TimedOut())
    assert await _reveal(context, session_factory) is None
    context.bot.send_photo.assert_not_awaited()
    context.bot.send_media_group.assert_awaited_once()
    assert _kept(session_factory, 3) == []
    finished.assert_not_called()


async def test_timed_out_video_vote_keeps_clues(session_factory, monkeypatch) -> None:
    _video_ok(monkeypatch)
    _add_clues(session_factory, 3, [b"c1", b"c2"])
    context = _context(session_factory)
    context.bot.send_video = AsyncMock(side_effect=TimedOut())
    assert await _reveal(context, session_factory, clear_clues=False) is None
    context.bot.send_media_group.assert_awaited_once()
    assert _kept(session_factory, 3) == [b"c1", b"c2"]


async def test_failed_reveal_skips_extras(session_factory, monkeypatch) -> None:
    _video_ok(monkeypatch)
    _add_clues(session_factory, 3, [b"c1", b"c2"])
    context = _context(session_factory)
    context.bot.send_video = AsyncMock(side_effect=TelegramError("bad"))
    context.bot.send_photo = AsyncMock(side_effect=TelegramError("down"))
    assert await _reveal(context, session_factory) is None
    context.bot.send_media_group.assert_not_awaited()
    assert _kept(session_factory, 3) == [b"c1", b"c2"]


async def test_extras_failure_does_not_block(session_factory, monkeypatch, log_records) -> None:
    _video_ok(monkeypatch)
    _add_clues(session_factory, 3, [b"c1", b"c2"])
    context = _context(session_factory)
    context.bot.send_media_group = AsyncMock(side_effect=TelegramError("down"))
    sent = await _reveal(context, session_factory)
    assert sent is not None
    assert sent.message_id == 11
    assert any(line.level == "ERROR" for line in log_records)


async def test_hard_mode_pair_video_alone(session_factory, monkeypatch) -> None:
    with session_scope(session_factory) as session:
        store.reserve(session, 3, RevealEffect.IRIS, "b")
    render = AsyncMock(return_value=b"mp4")
    monkeypatch.setattr(reveal, "render_reveal", render)
    monkeypatch.setattr(reveal, "finished", MagicMock())
    _add_clues(session_factory, 3, [b"c1"])
    context = _context(session_factory)
    sent = await current_image.post_reveal_pair(
        context,
        session_factory,
        target=RevealTarget(3, None),
        photos=(b"img-a", b"img-b"),
        caption="cap",
    )
    assert sent is not None
    assert sent.message_id == 11
    assert render.await_args is not None
    assert render.await_args.args[2] == b"img-b"
    assert context.bot.send_video.await_args.kwargs["caption"] == "cap"
    media = context.bot.send_media_group.await_args.kwargs["media"]
    assert [item.media.input_file_content for item in media] == [b"img-a", b"c1"]


async def test_hard_mode_pair_without_clues_sends_the_other_photo(
    session_factory, monkeypatch
) -> None:
    _video_ok(monkeypatch)
    context = _context(session_factory)
    await current_image.post_reveal_pair(
        context,
        session_factory,
        target=RevealTarget(3, None),
        photos=(b"img-a", b"img-b"),
        caption="cap",
    )
    assert context.bot.send_photo.await_args.kwargs["photo"] == b"img-b"


async def test_pair_vote_opening_keeps_clues(session_factory, monkeypatch) -> None:
    _video_ok(monkeypatch)
    _add_clues(session_factory, 3, [b"c1"])
    await current_image.post_reveal_pair(
        _context(session_factory),
        session_factory,
        target=RevealTarget(3, None, clear_clues=False),
        photos=(b"a", b"b"),
        caption="cap",
    )
    assert _kept(session_factory, 3) == [b"c1"]


def test_clear_image_if_sent_clears_clue_images(session_factory) -> None:
    _add_clues(session_factory, 3, [b"c1"])
    current_image.clear_image_if_sent(session_factory, 3, MagicMock())
    assert _kept(session_factory, 3) == []


def test_clear_image_if_sent_keeps_clue_images_when_not_sent(session_factory) -> None:
    _add_clues(session_factory, 3, [b"c1"])
    current_image.clear_image_if_sent(session_factory, 3, None)
    assert _kept(session_factory, 3) == [b"c1"]


async def test_album_prep_failure_does_not_block_the_reveal(
    session_factory, monkeypatch, log_records
) -> None:
    _video_ok(monkeypatch)
    monkeypatch.setattr(handouts, "revealed_images", MagicMock(side_effect=RuntimeError("db")))
    context = _context(session_factory)
    sent = await _reveal(context, session_factory)
    assert sent is not None
    assert sent.message_id == 11
    context.bot.send_media_group.assert_not_awaited()
    assert any(line.level == "ERROR" for line in log_records)


async def test_failed_reveal_logs_that_the_album_is_skipped(
    session_factory, monkeypatch, log_records
) -> None:
    _video_ok(monkeypatch)
    context = _context(session_factory)
    context.bot.send_video = AsyncMock(side_effect=TelegramError("bad"))
    context.bot.send_photo = AsyncMock(side_effect=TelegramError("down"))
    assert await _reveal(context, session_factory) is None
    assert any(
        line.level == "INFO" and "skipping the clue album" in line.message for line in log_records
    )


async def test_timed_out_video_logs_the_cleared_clue_count(
    session_factory, monkeypatch, log_records
) -> None:
    _video_ok(monkeypatch)
    _add_clues(session_factory, 3, [b"c1", b"c2"])
    context = _context(session_factory)
    context.bot.send_video = AsyncMock(side_effect=TimedOut())
    await _reveal(context, session_factory)
    [line] = [line for line in log_records if "clue image(s) after the reveal" in line.message]
    assert (line.level, line.extra["count"]) == ("INFO", 2)
