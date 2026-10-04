import io
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from PIL import Image
from telegram.error import BadRequest, TimedOut

from nani_pix_bot.services import pixelate as pixelate_service
from nani_pix_bot.services.clues import shop
from nani_pix_bot.services.search import shikimori
from tests.commands.shop.helpers import (
    balance,
    make_context,
    make_query,
    purchases,
    seed_game,
    set_currency,
    tap,
)

_URLS = ["https://x/u1.jpg", "https://x/u2.jpg", "https://x/u3.jpg"]


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (80, 40), (200, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def _image_context(session_factory):
    context = make_context(session_factory)
    response = MagicMock(content=b"raw")
    response.raise_for_status = MagicMock()
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    context.bot_data["search_client"] = client
    photo = MagicMock()
    photo.file_id = "FILE-1"
    context.bot.send_photo = AsyncMock(return_value=MagicMock(photo=[MagicMock(), photo]))
    return context


@pytest.fixture
def stub_screenshots(monkeypatch):
    async def fake(_client, _provider_id):
        return list(_URLS)

    monkeypatch.setattr(shikimori, "screenshots", fake)
    monkeypatch.setattr(pixelate_service, "pixelate", lambda *_: b"px")


def _owned_urls(session_factory) -> list[str | None]:
    return [p.screenshot_url for p in purchases(session_factory)]


async def test_screenshot_clue_skips_shown_and_owned_urls_and_charges_30(
    session_factory, stub_screenshots
) -> None:
    game_id = seed_game(session_factory, shikimori_id=1, shown_screenshot_urls=[_URLS[0]])
    set_currency(session_factory, 2, 100)
    context = _image_context(session_factory)

    await tap(context, make_query(f"shop:buy:{game_id}:screenshot"))
    assert balance(session_factory) == 70
    await tap(context, make_query(f"shop:buy:{game_id}:screenshot"))

    assert _owned_urls(session_factory) == [_URLS[1], _URLS[2]]
    assert balance(session_factory) == 25  # 30, then 30 + 15
    send = context.bot.send_photo.await_args
    assert send.kwargs["chat_id"] == 2
    assert send.kwargs["photo"] == b"px"
    assert send.kwargs["reply_markup"].inline_keyboard[0][0].callback_data.startswith("shop:share:")
    assert purchases(session_factory)[-1].telegram_file_id == "FILE-1"
    assert "extra screenshot" in context.bot.send_message.await_args.kwargs["text"]


async def test_no_unused_screenshot_left_charges_nothing(session_factory, monkeypatch) -> None:
    async def only_shown(_client, _provider_id):
        return [_URLS[0]]

    monkeypatch.setattr(shikimori, "screenshots", only_shown)
    game_id = seed_game(session_factory, shikimori_id=1, shown_screenshot_urls=[_URLS[0]])
    set_currency(session_factory, 2, 100)
    context = _image_context(session_factory)
    query = make_query(f"shop:buy:{game_id}:screenshot")

    await tap(context, query)

    assert "No other screenshots" in query.answer.await_args.args[0]
    assert query.answer.await_args.kwargs["show_alert"] is True
    assert balance(session_factory) == 100
    assert purchases(session_factory) == []
    context.bot.send_photo.assert_not_awaited()


async def test_screenshot_provider_failure_charges_nothing(session_factory, monkeypatch) -> None:
    async def boom(_client, _provider_id):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(shikimori, "screenshots", boom)
    game_id = seed_game(session_factory, shikimori_id=1)
    set_currency(session_factory, 2, 100)
    context = _image_context(session_factory)
    query = make_query(f"shop:buy:{game_id}:screenshot")

    await tap(context, query)

    assert "No other screenshots" in query.answer.await_args.args[0]
    assert balance(session_factory) == 100
    assert purchases(session_factory) == []


async def test_screenshot_download_failure_charges_nothing(
    session_factory, stub_screenshots
) -> None:
    game_id = seed_game(session_factory, shikimori_id=1)
    set_currency(session_factory, 2, 100)
    context = _image_context(session_factory)
    context.bot_data["search_client"].get = AsyncMock(side_effect=httpx.ConnectError("down"))
    query = make_query(f"shop:buy:{game_id}:screenshot")

    await tap(context, query)

    assert "No other screenshots" in query.answer.await_args.args[0]
    assert balance(session_factory) == 100


async def test_screenshot_send_failure_refunds(session_factory, stub_screenshots) -> None:
    game_id = seed_game(session_factory, shikimori_id=1)
    set_currency(session_factory, 2, 100)
    context = _image_context(session_factory)
    context.bot.send_photo = AsyncMock(side_effect=TimedOut())
    query = make_query(f"shop:buy:{game_id}:screenshot")

    await tap(context, query)

    assert balance(session_factory) == 100
    assert purchases(session_factory) == []
    assert "refunded" in query.answer.await_args.args[0]
    context.bot.send_message.assert_not_awaited()


async def test_screenshot_without_provider_id_is_unavailable(
    session_factory, stub_screenshots
) -> None:
    game_id = seed_game(session_factory)
    set_currency(session_factory, 2, 100)
    context = _image_context(session_factory)
    query = make_query(f"shop:buy:{game_id}:screenshot")

    await tap(context, query)

    assert "isn't available" in query.answer.await_args.args[0]
    assert balance(session_factory) == 100


async def test_tile_button_opens_grid_without_charge(session_factory) -> None:
    game_id = seed_game(session_factory)
    set_currency(session_factory, 2, 100)
    context = _image_context(session_factory)
    query = make_query(f"shop:buy:{game_id}:tile")

    await tap(context, query)

    grid = context.bot.send_message.await_args
    assert grid.kwargs["chat_id"] == 2
    assert "Pick a tile" in grid.kwargs["text"]
    assert len(grid.kwargs["reply_markup"].inline_keyboard) == shop.TILE_GRID
    assert balance(session_factory) == 100
    assert purchases(session_factory) == []


async def test_buying_a_tile_sends_the_reveal_and_marks_it_owned(session_factory) -> None:
    game_id = seed_game(session_factory, original_image=_png())
    set_currency(session_factory, 2, 100)
    context = _image_context(session_factory)
    query = make_query(f"shop:tile:{game_id}:5")

    await tap(context, query)

    send = context.bot.send_photo.await_args
    assert send.kwargs["chat_id"] == 2
    assert Image.open(io.BytesIO(send.kwargs["photo"])).size == (80, 40)
    assert "tiles" in send.kwargs["caption"]
    with session_factory() as session:
        assert shop.owned_tiles(session, game_id, 2) == {5}
    assert balance(session_factory) == 90
    assert purchases(session_factory)[0].telegram_file_id == "FILE-1"
    markup = query.edit_message_reply_markup.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][5].text == "✅"
    assert "unpixelated tile" in context.bot.send_message.await_args.kwargs["text"]


async def test_tile_grid_edit_ignores_message_not_modified(session_factory) -> None:
    game_id = seed_game(session_factory, original_image=_png())
    set_currency(session_factory, 2, 100)
    context = _image_context(session_factory)
    query = make_query(f"shop:tile:{game_id}:5")
    query.edit_message_reply_markup = AsyncMock(side_effect=BadRequest("Message is not modified"))

    await tap(context, query)

    assert balance(session_factory) == 90
    context.bot.send_message.assert_awaited_once()


async def test_tile_send_failure_refunds(session_factory) -> None:
    game_id = seed_game(session_factory, original_image=_png())
    set_currency(session_factory, 2, 100)
    context = _image_context(session_factory)
    context.bot.send_photo = AsyncMock(side_effect=TimedOut())
    query = make_query(f"shop:tile:{game_id}:5")

    await tap(context, query)

    assert balance(session_factory) == 100
    assert purchases(session_factory) == []
    query.edit_message_reply_markup.assert_not_awaited()


@pytest.mark.parametrize("index", ["64", "-1", "abc", "5.5"])
async def test_bad_tile_index_is_ignored_without_charge(session_factory, index) -> None:
    game_id = seed_game(session_factory, original_image=_png())
    set_currency(session_factory, 2, 100)
    context = _image_context(session_factory)
    query = make_query(f"shop:tile:{game_id}:{index}")

    await tap(context, query)

    query.answer.assert_awaited_once_with()
    assert balance(session_factory) == 100
    context.bot.send_photo.assert_not_awaited()


async def test_owned_tile_is_not_charged_twice(session_factory) -> None:
    game_id = seed_game(session_factory, original_image=_png())
    set_currency(session_factory, 2, 100)
    context = _image_context(session_factory)

    await tap(context, make_query(f"shop:tile:{game_id}:5"))
    second = make_query(f"shop:tile:{game_id}:5")
    await tap(context, second)

    assert balance(session_factory) == 90
    assert "already have" in second.answer.await_args.args[0]


async def test_tile_in_hard_mode_is_unavailable(session_factory) -> None:
    game_id = seed_game(session_factory, original_image=_png(), hard_mode=True)
    set_currency(session_factory, 2, 100)
    for data in (f"shop:buy:{game_id}:tile", f"shop:tile:{game_id}:5"):
        context = _image_context(session_factory)
        query = make_query(data)

        await tap(context, query)

        assert "isn't available" in query.answer.await_args.args[0]
        assert balance(session_factory) == 100
        context.bot.send_photo.assert_not_awaited()
