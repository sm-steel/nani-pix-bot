import io
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from PIL import Image
from telegram.error import BadRequest, TimedOut

from nani_pix_bot.models.enums import ClueKind
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
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

    await tap(context, make_query(f"shop:buy:{game_id}:screenshot:0"))
    assert balance(session_factory) == 70
    await tap(context, make_query(f"shop:buy:{game_id}:screenshot:1"))

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
    query = make_query(f"shop:buy:{game_id}:screenshot:0")

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
    query = make_query(f"shop:buy:{game_id}:screenshot:0")

    await tap(context, query)

    assert "Couldn't load a screenshot" in query.answer.await_args.args[0]
    assert balance(session_factory) == 100
    assert purchases(session_factory) == []


async def test_screenshot_download_failure_charges_nothing(
    session_factory, stub_screenshots
) -> None:
    game_id = seed_game(session_factory, shikimori_id=1)
    set_currency(session_factory, 2, 100)
    context = _image_context(session_factory)
    context.bot_data["search_client"].get = AsyncMock(side_effect=httpx.ConnectError("down"))
    query = make_query(f"shop:buy:{game_id}:screenshot:0")

    await tap(context, query)

    assert "Couldn't load a screenshot" in query.answer.await_args.args[0]
    assert balance(session_factory) == 100


async def test_empty_screenshot_list_still_says_none_left(session_factory, monkeypatch) -> None:
    async def nothing(_client, _provider_id):
        return []

    monkeypatch.setattr(shikimori, "screenshots", nothing)
    game_id = seed_game(session_factory, shikimori_id=1)
    set_currency(session_factory, 2, 100)
    query = make_query(f"shop:buy:{game_id}:screenshot:0")

    await tap(_image_context(session_factory), query)

    assert "No other screenshots" in query.answer.await_args.args[0]
    assert balance(session_factory) == 100


async def test_stale_screenshot_tap_is_refused_and_charges_once(
    session_factory, stub_screenshots
) -> None:
    game_id = seed_game(session_factory, shikimori_id=1)
    set_currency(session_factory, 2, 100)
    context = _image_context(session_factory)

    await tap(context, make_query(f"shop:buy:{game_id}:screenshot:0"))
    stale = make_query(f"shop:buy:{game_id}:screenshot:0")
    await tap(context, stale)

    assert balance(session_factory) == 70
    assert len(purchases(session_factory)) == 1
    assert "over" in stale.answer.await_args.args[0]
    assert stale.answer.await_args.kwargs["show_alert"] is True


@pytest.mark.parametrize("suffix", ["", ":x", ":0:1"])
async def test_malformed_screenshot_button_is_ignored(session_factory, suffix) -> None:
    game_id = seed_game(session_factory, shikimori_id=1)
    set_currency(session_factory, 2, 100)
    query = make_query(f"shop:buy:{game_id}:screenshot{suffix}")

    await tap(_image_context(session_factory), query)

    assert balance(session_factory) == 100
    assert purchases(session_factory) == []


async def test_other_kinds_reject_a_fifth_part(session_factory) -> None:
    game_id = seed_game(session_factory)
    set_currency(session_factory, 2, 100)

    await tap(make_context(session_factory), make_query(f"shop:buy:{game_id}:last_letter:0"))

    assert balance(session_factory) == 100


async def test_late_answer_does_not_skip_the_topic_notice(
    session_factory, stub_screenshots
) -> None:
    game_id = seed_game(session_factory, shikimori_id=1)
    set_currency(session_factory, 2, 100)
    context = _image_context(session_factory)
    query = make_query(f"shop:buy:{game_id}:screenshot:0")
    query.answer = AsyncMock(side_effect=BadRequest("Query is too old"))

    await tap(context, query)

    assert "extra screenshot" in context.bot.send_message.await_args.kwargs["text"]


async def test_screenshot_send_failure_refunds(session_factory, stub_screenshots) -> None:
    game_id = seed_game(session_factory, shikimori_id=1)
    set_currency(session_factory, 2, 100)
    context = _image_context(session_factory)
    context.bot.send_photo = AsyncMock(side_effect=TimedOut())
    query = make_query(f"shop:buy:{game_id}:screenshot:0")

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
    query = make_query(f"shop:buy:{game_id}:screenshot:0")

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
    assert "Pick the round" in grid.kwargs["text"]
    assert len(grid.kwargs["reply_markup"].inline_keyboard) == shop.TILE_GRID
    assert balance(session_factory) == 100
    assert purchases(session_factory) == []


async def test_buying_a_tile_sends_the_reveal_and_clears_the_grid(session_factory) -> None:
    game_id = seed_game(session_factory, original_image=_png())
    set_currency(session_factory, 2, 100)
    context = _image_context(session_factory)
    query = make_query(f"shop:tile:{game_id}:5")

    await tap(context, query)

    send = context.bot.send_photo.await_args
    assert send.kwargs["chat_id"] == 2
    assert Image.open(io.BytesIO(send.kwargs["photo"])).size == (80, 40)
    assert "tile" in send.kwargs["caption"]
    with session_factory() as session:
        assert shop.round_tile(session, game_id) == 5
    assert balance(session_factory) == 100 - _tile_price(session_factory, game_id)
    assert purchases(session_factory)[0].telegram_file_id == "FILE-1"
    assert query.edit_message_reply_markup.await_args.kwargs["reply_markup"] is None
    assert "unpixelated tile" in context.bot.send_message.await_args.kwargs["text"]


def _tile_price(session_factory, game_id: int) -> int:
    with session_factory() as session:
        game = session.get(Game, game_id)
        buyer = session.get(Player, 2)
        assert game is not None
        assert buyer is not None
        return shop.price(session, game, buyer, ClueKind.TILE)


async def test_tile_grid_edit_ignores_message_not_modified(session_factory) -> None:
    game_id = seed_game(session_factory, original_image=_png())
    set_currency(session_factory, 2, 100)
    context = _image_context(session_factory)
    query = make_query(f"shop:tile:{game_id}:5")
    query.edit_message_reply_markup = AsyncMock(side_effect=BadRequest("Message is not modified"))

    await tap(context, query)

    assert balance(session_factory) == 100 - _tile_price(session_factory, game_id)
    context.bot.send_message.assert_awaited_once()


async def test_later_buyer_gets_the_same_tile_without_a_grid(session_factory) -> None:
    game_id = seed_game(session_factory, original_image=_png())
    set_currency(session_factory, 2, 100)
    set_currency(session_factory, 3, 100)
    await tap(_image_context(session_factory), make_query(f"shop:tile:{game_id}:5"))
    context = _image_context(session_factory)
    query = make_query(f"shop:buy:{game_id}:tile", user_id=3)

    await tap(context, query)

    context.bot.send_photo.assert_awaited_once()
    assert context.bot.send_photo.await_args.kwargs["chat_id"] == 3
    # the only DM text is the topic-free purchase notice, never a grid
    for call in context.bot.send_message.await_args_list:
        assert "reply_markup" not in call.kwargs
    query.edit_message_reply_markup.assert_not_awaited()
    assert balance(session_factory, 3) == 100 - _tile_price(session_factory, game_id)
    assert [p.tile_index for p in purchases(session_factory)] == [5, 5]


async def test_forged_different_tile_after_the_choice_is_refused(session_factory) -> None:
    game_id = seed_game(session_factory, original_image=_png())
    set_currency(session_factory, 2, 100)
    set_currency(session_factory, 3, 100)
    await tap(_image_context(session_factory), make_query(f"shop:tile:{game_id}:5"))
    context = _image_context(session_factory)
    query = make_query(f"shop:tile:{game_id}:6", user_id=3)

    await tap(context, query)

    assert "isn't available" in query.answer.await_args.args[0]
    assert balance(session_factory, 3) == 100
    assert len(purchases(session_factory)) == 1
    context.bot.send_photo.assert_not_awaited()


async def test_stale_grid_tap_on_the_chosen_tile_is_accepted(session_factory) -> None:
    game_id = seed_game(session_factory, original_image=_png())
    set_currency(session_factory, 2, 100)
    set_currency(session_factory, 3, 100)
    await tap(_image_context(session_factory), make_query(f"shop:tile:{game_id}:5"))
    context = _image_context(session_factory)

    await tap(context, make_query(f"shop:tile:{game_id}:5", user_id=3))

    context.bot.send_photo.assert_awaited_once()
    assert balance(session_factory, 3) == 100 - _tile_price(session_factory, game_id)


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

    assert balance(session_factory) == 100 - _tile_price(session_factory, game_id)
    assert "already have" in second.answer.await_args.args[0]
    third = make_query(f"shop:buy:{game_id}:tile")
    await tap(context, third)
    assert "isn't available" in third.answer.await_args.args[0]
    assert len(purchases(session_factory)) == 1


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
