import io
import random
import zlib
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from PIL import Image
from sqlalchemy import select
from telegram.error import BadRequest, TimedOut

from nani_pix_bot.models import CluePurchase
from nani_pix_bot.models.enums import ClueKind
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import pixelate as pixelate_service
from nani_pix_bot.services.clues import handouts, shop
from nani_pix_bot.services.economy.config import EconomyKey
from nani_pix_bot.services.pixelate import fingerprint
from nani_pix_bot.services.search import shikimori
from tests.commands.shop.helpers import (
    PRICES,
    RICH,
    balance,
    make_context,
    make_query,
    purchases,
    seed_game,
    set_currency,
    tap,
)
from tests.frames import encode, frame, frame_bytes

_URLS = ["https://x/u1.jpg", "https://x/u2.jpg", "https://x/u3.jpg"]


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (80, 40), (200, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def _frame_of(url: str) -> bytes:
    """The picture behind a fake screenshot URL: a distinct frame per URL."""
    return frame_bytes(zlib.crc32(url.encode()))


def _image_context(session_factory, frames: dict[str, bytes] | None = None):
    """A context whose search client 'downloads' `frames[url]`, or a frame
    of its own for any other URL."""
    context = make_context(session_factory)

    async def download(url: str) -> MagicMock:
        response = MagicMock(content=(frames or {}).get(url) or _frame_of(url))
        response.raise_for_status = MagicMock()
        return response

    client = MagicMock()
    client.get = AsyncMock(side_effect=download)
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


def _owned_urls(session_factory) -> list[str]:
    return [p.screenshot_url or "" for p in purchases(session_factory)]


async def test_screenshot_clue_skips_shown_and_owned_urls_and_charges_the_escalating_price(
    session_factory, stub_screenshots
) -> None:
    game_id = seed_game(session_factory, shikimori_id=1, shown_screenshot_urls=[_URLS[0]])
    set_currency(session_factory, 2, RICH)
    context = _image_context(session_factory)

    await tap(context, make_query(f"shop:buy:{game_id}:screenshot:0"))
    assert balance(session_factory) == RICH - PRICES[EconomyKey.CLUE_SCREENSHOT]
    await tap(context, make_query(f"shop:buy:{game_id}:screenshot:1"))

    assert sorted(_owned_urls(session_factory)) == [_URLS[1], _URLS[2]]
    first = PRICES[EconomyKey.CLUE_SCREENSHOT]  # then the first price plus one step
    second = first + PRICES[EconomyKey.CLUE_SCREENSHOT_STEP]
    assert balance(session_factory) == RICH - first - second
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
    set_currency(session_factory, 2, RICH)
    context = _image_context(session_factory)
    query = make_query(f"shop:buy:{game_id}:screenshot:0")

    await tap(context, query)

    assert "No other screenshots" in query.answer.await_args.args[0]
    assert query.answer.await_args.kwargs["show_alert"] is True
    assert balance(session_factory) == RICH
    assert purchases(session_factory) == []
    context.bot.send_photo.assert_not_awaited()


async def test_screenshot_provider_failure_charges_nothing(session_factory, monkeypatch) -> None:
    async def boom(_client, _provider_id):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(shikimori, "screenshots", boom)
    game_id = seed_game(session_factory, shikimori_id=1)
    set_currency(session_factory, 2, RICH)
    context = _image_context(session_factory)
    query = make_query(f"shop:buy:{game_id}:screenshot:0")

    await tap(context, query)

    assert "Couldn't load a screenshot" in query.answer.await_args.args[0]
    assert balance(session_factory) == RICH
    assert purchases(session_factory) == []


async def test_screenshot_download_failure_charges_nothing(
    session_factory, stub_screenshots
) -> None:
    game_id = seed_game(session_factory, shikimori_id=1)
    set_currency(session_factory, 2, RICH)
    context = _image_context(session_factory)
    context.bot_data["search_client"].get = AsyncMock(side_effect=httpx.ConnectError("down"))
    query = make_query(f"shop:buy:{game_id}:screenshot:0")

    await tap(context, query)

    assert "Couldn't load a screenshot" in query.answer.await_args.args[0]
    assert balance(session_factory) == RICH


async def test_empty_screenshot_list_still_says_none_left(session_factory, monkeypatch) -> None:
    async def nothing(_client, _provider_id):
        return []

    monkeypatch.setattr(shikimori, "screenshots", nothing)
    game_id = seed_game(session_factory, shikimori_id=1)
    set_currency(session_factory, 2, RICH)
    query = make_query(f"shop:buy:{game_id}:screenshot:0")

    await tap(_image_context(session_factory), query)

    assert "No other screenshots" in query.answer.await_args.args[0]
    assert balance(session_factory) == RICH


async def test_stale_screenshot_tap_is_refused_and_charges_once(
    session_factory, stub_screenshots
) -> None:
    game_id = seed_game(session_factory, shikimori_id=1)
    set_currency(session_factory, 2, RICH)
    context = _image_context(session_factory)

    await tap(context, make_query(f"shop:buy:{game_id}:screenshot:0"))
    stale = make_query(f"shop:buy:{game_id}:screenshot:0")
    await tap(context, stale)

    assert balance(session_factory) == RICH - PRICES[EconomyKey.CLUE_SCREENSHOT]
    assert len(purchases(session_factory)) == 1
    assert "over" in stale.answer.await_args.args[0]
    assert stale.answer.await_args.kwargs["show_alert"] is True


@pytest.mark.parametrize("suffix", ["", ":x", ":0:1"])
async def test_malformed_screenshot_button_is_ignored(session_factory, suffix) -> None:
    game_id = seed_game(session_factory, shikimori_id=1)
    set_currency(session_factory, 2, RICH)
    query = make_query(f"shop:buy:{game_id}:screenshot{suffix}")

    await tap(_image_context(session_factory), query)

    assert balance(session_factory) == RICH
    assert purchases(session_factory) == []


async def test_other_kinds_reject_a_fifth_part(session_factory) -> None:
    game_id = seed_game(session_factory)
    set_currency(session_factory, 2, RICH)

    await tap(make_context(session_factory), make_query(f"shop:buy:{game_id}:last_letter:0"))

    assert balance(session_factory) == RICH


async def test_late_answer_does_not_skip_the_topic_notice(
    session_factory, stub_screenshots
) -> None:
    game_id = seed_game(session_factory, shikimori_id=1)
    set_currency(session_factory, 2, RICH)
    context = _image_context(session_factory)
    query = make_query(f"shop:buy:{game_id}:screenshot:0")
    query.answer = AsyncMock(side_effect=BadRequest("Query is too old"))

    await tap(context, query)

    assert "extra screenshot" in context.bot.send_message.await_args.kwargs["text"]


async def test_screenshot_send_failure_refunds(session_factory, stub_screenshots) -> None:
    game_id = seed_game(session_factory, shikimori_id=1)
    set_currency(session_factory, 2, RICH)
    context = _image_context(session_factory)
    context.bot.send_photo = AsyncMock(side_effect=TimedOut())
    query = make_query(f"shop:buy:{game_id}:screenshot:0")

    await tap(context, query)

    assert balance(session_factory) == RICH
    assert purchases(session_factory) == []
    assert "refunded" in query.answer.await_args.args[0]
    context.bot.send_message.assert_not_awaited()


async def test_screenshot_without_provider_id_is_unavailable(
    session_factory, stub_screenshots
) -> None:
    game_id = seed_game(session_factory)
    set_currency(session_factory, 2, RICH)
    context = _image_context(session_factory)
    query = make_query(f"shop:buy:{game_id}:screenshot:0")

    await tap(context, query)

    assert "isn't available" in query.answer.await_args.args[0]
    assert balance(session_factory) == RICH


@pytest.fixture
def in_list_order(monkeypatch) -> None:
    """Candidates tried in the provider's order instead of shuffled."""
    monkeypatch.setattr(random, "shuffle", lambda _seq: None)


def _buy_screenshot_as(context, game_id: int, user_id: int, owned: int = 0):
    return tap(context, make_query(f"shop:buy:{game_id}:screenshot:{owned}", user_id=user_id))


async def test_another_buyer_never_gets_a_screenshot_someone_already_got(
    session_factory, stub_screenshots
) -> None:
    game_id = seed_game(session_factory, shikimori_id=1, shown_screenshot_urls=[_URLS[0]])
    set_currency(session_factory, 2, RICH)
    set_currency(session_factory, 3, RICH)
    context = _image_context(session_factory)

    await _buy_screenshot_as(context, game_id, 2)
    await _buy_screenshot_as(context, game_id, 3)

    assert sorted(_owned_urls(session_factory)) == [_URLS[1], _URLS[2]]


async def test_rebuying_after_a_refund_gives_a_new_screenshot(
    session_factory, stub_screenshots
) -> None:
    game_id = seed_game(session_factory, shikimori_id=1)
    set_currency(session_factory, 2, RICH)
    context = _image_context(session_factory)
    await _buy_screenshot_as(context, game_id, 2)
    [first] = _owned_urls(session_factory)
    with session_factory() as session:
        shop.refund(session, session.scalars(select(CluePurchase)).one())
        session.commit()

    await _buy_screenshot_as(context, game_id, 2)

    [second] = _owned_urls(session_factory)
    assert second != first


async def test_the_pick_is_random_among_unused_screenshots(
    session_factory, stub_screenshots, monkeypatch
) -> None:
    monkeypatch.setattr(random, "shuffle", lambda seq: seq.reverse())
    game_id = seed_game(session_factory, shikimori_id=1)
    set_currency(session_factory, 2, RICH)

    await _buy_screenshot_as(_image_context(session_factory), game_id, 2)

    assert _owned_urls(session_factory) == [_URLS[2]]


async def test_an_uploaded_rounds_own_frame_is_never_sold(
    session_factory, stub_screenshots, in_list_order, log_records
) -> None:
    # The starter uploaded a re-encoded copy of what Shikimori serves as u1.
    upload = encode(frame(1).resize((320, 180)), "JPEG", quality=70)
    game_id = seed_game(session_factory, shikimori_id=1, original_image=upload)
    set_currency(session_factory, 2, RICH)
    context = _image_context(session_factory, {_URLS[0]: frame_bytes(1)})

    await _buy_screenshot_as(context, game_id, 2)

    assert _owned_urls(session_factory) == [_URLS[1]]
    [skip] = [r for r in log_records if "skipped" in r.message]
    assert skip.extra["screenshot_url"] == _URLS[0]
    assert skip.extra["seen_as"] == "the round's own image"


async def test_hard_mode_pair_frames_are_never_sold(
    session_factory, stub_screenshots, in_list_order
) -> None:
    game_id = seed_game(
        session_factory,
        shikimori_id=1,
        hard_mode=True,
        hard_mode_turn=1,
        current_stage=None,
        original_image=None,
        hard_mode_image_a=frame_bytes(1),
        hard_mode_image_b=frame_bytes(2),
    )
    set_currency(session_factory, 2, RICH)
    frames = {_URLS[0]: frame_bytes(1), _URLS[1]: frame_bytes(2)}

    await _buy_screenshot_as(_image_context(session_factory, frames), game_id, 2)

    assert _owned_urls(session_factory) == [_URLS[2]]


async def test_a_handed_out_frame_behind_another_url_is_skipped(
    session_factory, monkeypatch, in_list_order
) -> None:
    mirror = "https://x/u1-mirror.jpg"

    async def listing(_client, _provider_id):
        return [_URLS[0], mirror, _URLS[1]]

    monkeypatch.setattr(shikimori, "screenshots", listing)
    monkeypatch.setattr(pixelate_service, "pixelate", lambda *_: b"px")
    game_id = seed_game(session_factory, shikimori_id=1)
    set_currency(session_factory, 2, RICH)
    set_currency(session_factory, 3, RICH)
    frames = {_URLS[0]: frame_bytes(1), mirror: frame_bytes(1)}
    context = _image_context(session_factory, frames)

    await _buy_screenshot_as(context, game_id, 2)
    await _buy_screenshot_as(context, game_id, 3)

    assert _owned_urls(session_factory) == [_URLS[0], _URLS[1]]


async def test_only_already_seen_frames_left_charges_nothing(session_factory, monkeypatch) -> None:
    async def listing(_client, _provider_id):
        return [_URLS[0]]

    monkeypatch.setattr(shikimori, "screenshots", listing)
    game_id = seed_game(session_factory, shikimori_id=1, original_image=frame_bytes(1))
    set_currency(session_factory, 2, RICH)
    query = make_query(f"shop:buy:{game_id}:screenshot:0")

    await tap(_image_context(session_factory, {_URLS[0]: frame_bytes(1)}), query)

    assert "No other screenshots" in query.answer.await_args.args[0]
    assert balance(session_factory) == RICH
    assert purchases(session_factory) == []


async def test_the_handout_keeps_the_frames_fingerprint(session_factory, stub_screenshots) -> None:
    game_id = seed_game(session_factory, shikimori_id=1)
    set_currency(session_factory, 2, RICH)

    await _buy_screenshot_as(_image_context(session_factory), game_id, 2)

    with session_factory() as session:
        [row] = handouts.for_game(session, game_id)
    assert row.fingerprint == fingerprint.fingerprint(_frame_of(row.screenshot_url))


async def test_tile_button_opens_grid_without_charge(session_factory) -> None:
    game_id = seed_game(session_factory)
    set_currency(session_factory, 2, RICH)
    context = _image_context(session_factory)
    query = make_query(f"shop:buy:{game_id}:tile")

    await tap(context, query)

    grid = context.bot.send_message.await_args
    assert grid.kwargs["chat_id"] == 2
    assert "Pick the round" in grid.kwargs["text"]
    assert len(grid.kwargs["reply_markup"].inline_keyboard) == shop.TILE_GRID
    assert balance(session_factory) == RICH
    assert purchases(session_factory) == []


async def test_buying_a_tile_sends_the_reveal_and_clears_the_grid(session_factory) -> None:
    game_id = seed_game(session_factory, original_image=_png())
    set_currency(session_factory, 2, RICH)
    context = _image_context(session_factory)
    query = make_query(f"shop:tile:{game_id}:5")

    await tap(context, query)

    send = context.bot.send_photo.await_args
    assert send.kwargs["chat_id"] == 2
    assert Image.open(io.BytesIO(send.kwargs["photo"])).size == (80, 40)
    assert "tile" in send.kwargs["caption"]
    with session_factory() as session:
        assert shop.round_tile(session, game_id) == 5
    assert balance(session_factory) == RICH - _tile_price(session_factory, game_id)
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
    set_currency(session_factory, 2, RICH)
    context = _image_context(session_factory)
    query = make_query(f"shop:tile:{game_id}:5")
    query.edit_message_reply_markup = AsyncMock(side_effect=BadRequest("Message is not modified"))

    await tap(context, query)

    assert balance(session_factory) == RICH - _tile_price(session_factory, game_id)
    context.bot.send_message.assert_awaited_once()


async def test_later_buyer_gets_the_same_tile_without_a_grid(session_factory) -> None:
    game_id = seed_game(session_factory, original_image=_png())
    set_currency(session_factory, 2, RICH)
    set_currency(session_factory, 3, RICH)
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
    assert balance(session_factory, 3) == RICH - _tile_price(session_factory, game_id)
    assert [p.tile_index for p in purchases(session_factory)] == [5, 5]


async def test_refunded_first_tile_lets_the_next_buyer_pick_again(session_factory) -> None:
    game_id = seed_game(session_factory, original_image=_png())
    set_currency(session_factory, 2, RICH)
    set_currency(session_factory, 3, RICH)
    failing = _image_context(session_factory)
    failing.bot.send_photo = AsyncMock(side_effect=TimedOut())
    await tap(failing, make_query(f"shop:tile:{game_id}:5"))
    assert balance(session_factory) == RICH
    assert purchases(session_factory) == []
    with session_factory() as session:
        assert shop.round_tile(session, game_id) is None
    context = _image_context(session_factory)

    await tap(context, make_query(f"shop:buy:{game_id}:tile", user_id=3))

    grid = context.bot.send_message.await_args
    assert grid.kwargs["chat_id"] == 3
    assert len(grid.kwargs["reply_markup"].inline_keyboard) == shop.TILE_GRID
    assert balance(session_factory, 3) == RICH
    assert purchases(session_factory) == []


async def test_forged_different_tile_after_the_choice_is_refused(session_factory) -> None:
    game_id = seed_game(session_factory, original_image=_png())
    set_currency(session_factory, 2, RICH)
    set_currency(session_factory, 3, RICH)
    await tap(_image_context(session_factory), make_query(f"shop:tile:{game_id}:5"))
    context = _image_context(session_factory)
    query = make_query(f"shop:tile:{game_id}:6", user_id=3)

    await tap(context, query)

    assert "isn't available" in query.answer.await_args.args[0]
    assert balance(session_factory, 3) == RICH
    assert len(purchases(session_factory)) == 1
    context.bot.send_photo.assert_not_awaited()


async def test_stale_grid_tap_on_the_chosen_tile_is_accepted(session_factory) -> None:
    game_id = seed_game(session_factory, original_image=_png())
    set_currency(session_factory, 2, RICH)
    set_currency(session_factory, 3, RICH)
    await tap(_image_context(session_factory), make_query(f"shop:tile:{game_id}:5"))
    context = _image_context(session_factory)

    await tap(context, make_query(f"shop:tile:{game_id}:5", user_id=3))

    context.bot.send_photo.assert_awaited_once()
    assert balance(session_factory, 3) == RICH - _tile_price(session_factory, game_id)


async def test_tile_send_failure_refunds(session_factory) -> None:
    game_id = seed_game(session_factory, original_image=_png())
    set_currency(session_factory, 2, RICH)
    context = _image_context(session_factory)
    context.bot.send_photo = AsyncMock(side_effect=TimedOut())
    query = make_query(f"shop:tile:{game_id}:5")

    await tap(context, query)

    assert balance(session_factory) == RICH
    assert purchases(session_factory) == []
    query.edit_message_reply_markup.assert_not_awaited()


@pytest.mark.parametrize("index", ["64", "-1", "abc", "5.5"])
async def test_bad_tile_index_is_ignored_without_charge(session_factory, index) -> None:
    game_id = seed_game(session_factory, original_image=_png())
    set_currency(session_factory, 2, RICH)
    context = _image_context(session_factory)
    query = make_query(f"shop:tile:{game_id}:{index}")

    await tap(context, query)

    query.answer.assert_awaited_once_with()
    assert balance(session_factory) == RICH
    context.bot.send_photo.assert_not_awaited()


async def test_owned_tile_is_not_charged_twice(session_factory) -> None:
    game_id = seed_game(session_factory, original_image=_png())
    set_currency(session_factory, 2, RICH)
    context = _image_context(session_factory)

    await tap(context, make_query(f"shop:tile:{game_id}:5"))
    second = make_query(f"shop:tile:{game_id}:5")
    await tap(context, second)

    assert balance(session_factory) == RICH - _tile_price(session_factory, game_id)
    assert "already have" in second.answer.await_args.args[0]
    third = make_query(f"shop:buy:{game_id}:tile")
    await tap(context, third)
    assert "isn't available" in third.answer.await_args.args[0]
    assert len(purchases(session_factory)) == 1


async def test_tile_in_hard_mode_is_unavailable(session_factory) -> None:
    game_id = seed_game(session_factory, original_image=_png(), hard_mode=True)
    set_currency(session_factory, 2, RICH)
    for data in (f"shop:buy:{game_id}:tile", f"shop:tile:{game_id}:5"):
        context = _image_context(session_factory)
        query = make_query(data)

        await tap(context, query)

        assert "isn't available" in query.answer.await_args.args[0]
        assert balance(session_factory) == RICH
        context.bot.send_photo.assert_not_awaited()


async def test_tile_purchase_logs_its_delivery_at_info(session_factory, log_records) -> None:
    game_id = seed_game(session_factory, original_image=_png())
    set_currency(session_factory, 2, RICH)
    context = _image_context(session_factory)

    await tap(context, make_query(f"shop:tile:{game_id}:5"))

    info = [r for r in log_records if r.level == "INFO"]
    (bought,) = [r for r in info if r.message.startswith("bought the tile clue (tile 5) for ")]
    (dm,) = [r for r in info if r.message.startswith("DM'd the tile clue (purchase ")]
    assert (bought.extra["kind"], bought.extra["tile"], bought.extra["game_id"]) == (
        "tile",
        5,
        game_id,
    )
    assert (dm.extra["game_id"], dm.extra["purchase_id"]) == (game_id, bought.extra["purchase_id"])
