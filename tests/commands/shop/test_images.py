"""The extra-screenshot clue picks by the game's clue screenshot mode
(issue #450): the author's list first, random frames only in Random or
Both mode."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from nani_pix_bot.commands.shop import images
from nani_pix_bot.models import ClueAuthorScreenshot, ClueScreenshotHandout
from nani_pix_bot.models.enums import ClueScreenshotMode, PixelAlgorithm, Provider
from nani_pix_bot.services import pixelate as pixelate_service
from nani_pix_bot.services.pixelate import fingerprint
from nani_pix_bot.services.search import shikimori
from tests.commands.shop.helpers import (
    RICH,
    make_context,
    make_query,
    purchases,
    seed_game,
    set_currency,
    tap,
)
from tests.frames import frame_bytes

_RANDOM_URL = "https://x/random.jpg"


@pytest.fixture
def provider(monkeypatch) -> AsyncMock:
    """Shikimori's screenshot list, stubbed: one random frame."""
    screenshots = AsyncMock(return_value=[_RANDOM_URL])
    monkeypatch.setattr(shikimori, "screenshots", screenshots)
    monkeypatch.setattr(pixelate_service, "pixelate", lambda *_: b"px")
    return screenshots


def _context(session_factory) -> MagicMock:
    context = make_context(session_factory)

    async def download(_url: str) -> MagicMock:
        response = MagicMock(content=frame_bytes(99))
        response.raise_for_status = MagicMock()
        return response

    client = MagicMock()
    client.get = AsyncMock(side_effect=download)
    context.bot_data["search_client"] = client
    photo = MagicMock(file_id="FILE-1")
    context.bot.send_photo = AsyncMock(return_value=MagicMock(photo=[photo]))
    return context


def _candidate(seed: int, key: str) -> images.AuthorCandidate:
    image = frame_bytes(seed)
    return images.AuthorCandidate(key=key, image=image, fingerprint=fingerprint.fingerprint(image))


def _plan(mode: ClueScreenshotMode, author: tuple[images.AuthorCandidate, ...]):
    return images.ScreenshotPlan(
        "en",
        frozenset(),
        ((Provider.SHIKIMORI, 1),),
        64,
        PixelAlgorithm.BOX,
        mode=mode,
        author=author,
    )


async def test_fetch_author_first_then_random_in_both(session_factory, provider) -> None:
    context = _context(session_factory)
    entry = _candidate(1, "author:7")

    first = await images.fetch_extra_screenshot(context, _plan(ClueScreenshotMode.BOTH, (entry,)))

    assert first is not None
    assert (first.url, first.original, first.from_author) == ("author:7", entry.image, True)
    provider.assert_not_awaited()
    context.bot_data["search_client"].get.assert_not_awaited()

    second = await images.fetch_extra_screenshot(context, _plan(ClueScreenshotMode.BOTH, ()))

    assert second is not None
    assert (second.url, second.from_author) == (_RANDOM_URL, False)
    provider.assert_awaited_once()


async def test_fetch_author_mode_never_random(session_factory, provider) -> None:
    context = _context(session_factory)

    assert (
        await images.fetch_extra_screenshot(context, _plan(ClueScreenshotMode.AUTHOR, ())) is None
    )

    provider.assert_not_awaited()


async def test_fetch_skips_author_entry_already_seen(session_factory, provider) -> None:
    context = _context(session_factory)
    seen, fresh = _candidate(1, "author:1"), _candidate(2, "author:2")
    plan = images.ScreenshotPlan(
        "en",
        frozenset(),
        (),
        64,
        PixelAlgorithm.BOX,
        seen=((seen.fingerprint, "an earlier clue"),),
        mode=ClueScreenshotMode.AUTHOR,
        author=(seen, fresh),
    )

    fetched = await images.fetch_extra_screenshot(context, plan)

    assert fetched is not None
    assert fetched.url == "author:2"


def _seed_author_game(session_factory, mode: ClueScreenshotMode, **overrides) -> tuple[int, int]:
    game_id = seed_game(session_factory, clue_screenshot_mode=mode, **overrides)
    with session_factory() as session:
        image = frame_bytes(5)
        entry = ClueAuthorScreenshot(
            game_id=game_id, image=image, fingerprint=fingerprint.fingerprint(image)
        )
        session.add(entry)
        session.commit()
        return game_id, entry.id


async def test_buying_an_upload_records_its_author_key(session_factory, provider) -> None:
    game_id, entry_id = _seed_author_game(session_factory, ClueScreenshotMode.AUTHOR)
    set_currency(session_factory, 2, RICH)
    context = _context(session_factory)

    await tap(context, make_query(f"shop:buy:{game_id}:screenshot:0"))

    assert [p.screenshot_url for p in purchases(session_factory)] == [f"author:{entry_id}"]
    provider.assert_not_awaited()


async def test_author_entry_original_kept_for_reveal(session_factory, provider) -> None:
    game_id, _ = _seed_author_game(session_factory, ClueScreenshotMode.AUTHOR)
    set_currency(session_factory, 2, RICH)
    context = _context(session_factory)

    await tap(context, make_query(f"shop:buy:{game_id}:screenshot:0"))

    with session_factory() as session:
        [handout] = session.scalars(select(ClueScreenshotHandout)).all()
        assert handout.image == frame_bytes(5)
        assert handout.fingerprint == fingerprint.fingerprint(frame_bytes(5))


async def test_author_mode_used_up_refuses_without_charge(session_factory, provider) -> None:
    game_id, _ = _seed_author_game(session_factory, ClueScreenshotMode.AUTHOR, shikimori_id=1)
    set_currency(session_factory, 2, RICH)
    set_currency(session_factory, 3, RICH)
    context = _context(session_factory)
    await tap(context, make_query(f"shop:buy:{game_id}:screenshot:0", user_id=2))
    late = make_query(f"shop:buy:{game_id}:screenshot:0", user_id=3)

    await tap(context, late)

    assert len(purchases(session_factory)) == 1
    provider.assert_not_awaited()
    assert late.answer.await_args.kwargs.get("show_alert") is True


def test_random_mode_plan_has_no_author_candidates(session_factory) -> None:
    """Random mode is today's pick, unchanged: the stored list is unused."""
    game_id, _ = _seed_author_game(session_factory, ClueScreenshotMode.RANDOM, shikimori_id=1)
    set_currency(session_factory, 2, RICH)
    user = MagicMock(id=2, username="user2")

    with session_factory() as session:
        plan = images.screenshot_plan(session, user, game_id)

    assert plan.mode == ClueScreenshotMode.RANDOM
    assert plan.author == ()


async def test_random_mode_buy_sells_a_random_frame_not_the_list(session_factory, provider) -> None:
    game_id, _ = _seed_author_game(session_factory, ClueScreenshotMode.RANDOM, shikimori_id=1)
    set_currency(session_factory, 2, RICH)

    await tap(_context(session_factory), make_query(f"shop:buy:{game_id}:screenshot:0"))

    assert [p.screenshot_url for p in purchases(session_factory)] == [_RANDOM_URL]
    provider.assert_awaited_once()
