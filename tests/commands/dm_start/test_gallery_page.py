"""_show_gallery_page's handling of Telegram failing to deliver an album of
provider URLs (issue #283): a timeout isn't a refusal, and a refused URL
fetch is worth one retry with bytes we download ourselves."""

from unittest.mock import AsyncMock, MagicMock

import httpx
from telegram.error import BadRequest, TimedOut

from nani_pix_bot.commands.dm_start import screenshots
from nani_pix_bot.commands.dm_start.screenshots import GalleryTarget
from nani_pix_bot.models.enums import Provider

URLS = [f"https://shikimori.io/x/{i}.jpg" for i in range(8)]
TARGET = GalleryTarget(chat_id=1, provider=Provider.SHIKIMORI, offset=5)


def _context() -> MagicMock:
    context = MagicMock()
    context.bot_data = {"search_client": MagicMock()}
    context.bot.send_message = AsyncMock()
    context.bot.send_media_group = AsyncMock()
    context.bot.send_photo = AsyncMock()
    return context


def _downloads(context: MagicMock, *, fail: frozenset[str] | set[str] = frozenset()) -> AsyncMock:
    async def get(url: str) -> MagicMock:
        if url in fail:
            raise httpx.ConnectError("boom")
        response = MagicMock(content=url.encode())
        response.raise_for_status = MagicMock()
        return response

    context.bot_data["search_client"].get = AsyncMock(side_effect=get)
    return context.bot_data["search_client"].get


async def test_the_album_is_sent_with_a_long_read_timeout() -> None:
    context = _context()

    assert await screenshots._show_gallery_page(context, TARGET, URLS, "en") is None

    _, kwargs = context.bot.send_media_group.await_args
    assert kwargs["read_timeout"] == screenshots.GALLERY_READ_TIMEOUT
    context.bot.send_message.assert_awaited_once()  # the buttons


async def test_a_timed_out_album_still_gets_its_buttons(log_records) -> None:
    context = _context()
    context.bot.send_media_group.side_effect = TimedOut()

    failure = await screenshots._show_gallery_page(context, TARGET, URLS, "en")

    assert failure is None
    context.bot.send_message.assert_awaited_once()
    warning = next(r for r in log_records if r.level == "WARNING")
    assert warning.extra["count"] == 3  # the page, not the gallery


async def test_a_refused_fetch_is_resent_as_downloaded_bytes(log_records) -> None:
    context = _context()
    context.bot.send_media_group.side_effect = [BadRequest("webpage_curl_failed"), None]
    get = _downloads(context, fail={URLS[6]})

    failure = await screenshots._show_gallery_page(context, TARGET, URLS, "en")

    assert failure is None
    assert [c.args[0] for c in get.await_args_list] == URLS[5:8]
    _, kwargs = context.bot.send_media_group.await_args
    assert [m.caption for m in kwargs["media"]] == ["6", "8"]  # numbers kept
    context.bot.send_message.assert_awaited_once()
    assert not any(r.level == "ERROR" for r in log_records)


async def test_a_single_downloaded_screenshot_goes_out_as_a_photo() -> None:
    context = _context()
    context.bot.send_media_group.side_effect = BadRequest("webpage_curl_failed")
    _downloads(context, fail={URLS[5], URLS[6]})

    failure = await screenshots._show_gallery_page(context, TARGET, URLS, "en")

    assert failure is None
    context.bot.send_photo.assert_awaited_once()
    assert context.bot.send_photo.await_args.kwargs["caption"] == "8"


async def test_the_provider_is_down_only_when_our_downloads_fail_too(log_records) -> None:
    context = _context()
    context.bot.send_media_group.side_effect = BadRequest("webpage_curl_failed")
    _downloads(context, fail=set(URLS))

    failure = await screenshots._show_gallery_page(context, TARGET, URLS, "en")

    assert failure is not None
    assert failure.key == "dm_start.screenshot_service_down"
    context.bot.send_message.assert_not_awaited()
    error = next(r for r in log_records if r.level == "ERROR")
    assert error.extra["count"] == 3
