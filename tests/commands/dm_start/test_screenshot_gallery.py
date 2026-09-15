from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from loguru import logger
from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start import preview, screenshot_gallery, search
from nani_pix_bot.commands.dm_start.keyboards import SCREENSHOT_UPLOAD_CALLBACK_DATA
from nani_pix_bot.commands.dm_start.screenshots import SourceMenu
from nani_pix_bot.models.enums import SetupStep
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n
from nani_pix_bot.services.search import shikimori, tmdb
from nani_pix_bot.services.search.tmdb import TMDBResult

_FRIEREN_TMDB = TMDBResult(
    tmdb_id=209867,
    title_romaji=None,
    title_english="Frieren: Beyond Journey's End",
    title_native=None,
    synonyms=[],
)


def _make_context(session_factory, **extra_bot_data) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "search_client": MagicMock(),
        "tmdb_client": MagicMock(),
        **extra_bot_data,
    }
    context.bot.send_message = AsyncMock()
    context.bot.send_media_group = AsyncMock()
    return context


def _make_callback_update(*, data: str, user_id: int = 1) -> MagicMock:
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.from_user.id = user_id
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


def _make_text_update(*, text: str, user_id: int = 1) -> MagicMock:
    """A plain DM text message — what the starter types at any of the
    screenshot sub-flow's "type a search query" prompts."""
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.type = "private"
    update.message.text = text
    update.message.reply_text = AsyncMock()
    return update


@contextmanager
def _captured_warnings(fmt: str = "{message}") -> Iterator[list[str]]:
    """Every WARNING+ line the code under test emits. A silent return is
    only acceptable on screen if it is *not* silent in the logs — which
    is a property of the handler worth pinning, not an implementation
    detail, since the whole point is that production has nothing else to
    go on when a starter reports "I tapped it and nothing happened".

    `fmt` defaults to the message alone; pass one carrying `{name}`/
    `{function}`/`{line}` to assert on which call site a line came
    from, which is the half a shared log helper can quietly erase."""
    captured: list[str] = []
    sink_id = logger.add(captured.append, level="WARNING", format=fmt)
    try:
        yield captured
    finally:
        logger.remove(sink_id)


def _staged_game(session_factory, *, starter_id: int = 1, **overrides) -> int:
    """A SETUP game with identification already staged but no image
    yet — where every /newgame game sits right before the screenshot
    picker."""
    with session_factory() as session:
        session.add(Player(telegram_user_id=starter_id))
        session.commit()
        game = game_service.create_setup_game(session, starter_id=starter_id)
        game.source = overrides.pop("source", "shikimori")
        game.title_english = "Frieren: Beyond Journey's End"
        for key, value in overrides.items():
            setattr(game, key, value)
        session.commit()
        return game.id


async def test_screenshot_gallery_callback_handler_more_shows_the_next_page(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    urls = [f"https://shikimori.io/x/{i}.jpg" for i in range(8)]
    monkeypatch.setattr(shikimori, "screenshots", AsyncMock(return_value=urls))
    _staged_game(session_factory, shikimori_id=52991)

    update = _make_callback_update(data="screenshot_more:shikimori:5")
    context = _make_context(session_factory)

    await screenshot_gallery.screenshot_gallery_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    _, kwargs = context.bot.send_media_group.await_args
    captions = [item.caption for item in kwargs["media"]]
    assert captions == ["6", "7", "8"]  # 1-indexed, starting at offset 5
    update.callback_query.edit_message_text.assert_awaited_once()


async def test_screenshot_gallery_callback_handler_pick_downloads_and_shows_preview(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    urls = ["https://shikimori.io/x/0.jpg", "https://shikimori.io/x/1.jpg"]
    monkeypatch.setattr(shikimori, "screenshots", AsyncMock(return_value=urls))
    monkeypatch.setattr(preview.pixelate_service, "pixelate", lambda data, stage: b"pixelated")
    game_id = _staged_game(session_factory, shikimori_id=52991)

    update = _make_callback_update(data="screenshot_pick:shikimori:1")
    context = _make_context(session_factory)
    download_response = MagicMock(content=b"real-screenshot-bytes")
    download_response.raise_for_status = MagicMock()
    context.bot_data["search_client"].get = AsyncMock(return_value=download_response)

    await screenshot_gallery.screenshot_gallery_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.original_image == b"real-screenshot-bytes"
        # The one place image provenance is written: these bytes really
        # do come from the shikimori_id on file.
        assert fetched.screenshot_source == "shikimori"
        # ...and the picker is done, so nothing is being resolved.
        assert fetched.screenshot_picker_provider is None
        assert fetched.setup_step == SetupStep.CONFIRMING

    context.bot.send_media_group.assert_awaited_once()  # the confirmation preview album
    update.callback_query.edit_message_text.assert_awaited_once()


def _source_callbacks(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def _tmdb_menu() -> SourceMenu:
    return SourceMenu(providers=["shikimori", "jikan", "tmdb"], provider="tmdb")


async def test_screenshot_gallery_callback_handler_pick_falls_back_when_the_fetch_is_now_empty(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: a re-fetch (to resolve the tapped index into a real
    URL) that now comes back empty used to be treated as a silent
    stale-index no-op — leaving PICKING_SCREENSHOT in place with no way
    forward, since the source-selection/gallery messages are already
    gone. It must re-offer the source menu, same as the initial fetch
    finding nothing."""
    monkeypatch.setattr(shikimori, "screenshots", AsyncMock(return_value=[]))
    game_id = _staged_game(session_factory, shikimori_id=52991)

    update = _make_callback_update(data="screenshot_pick:shikimori:0")
    context = _make_context(session_factory)

    await screenshot_gallery.screenshot_gallery_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.setup_step == SetupStep.PICKING_SCREENSHOT
    update.callback_query.edit_message_text.assert_awaited_once()
    args, kwargs = update.callback_query.edit_message_text.await_args
    assert "screenshots" in args[0].lower()
    assert _source_callbacks(kwargs["reply_markup"]) == [
        "screenshot_source:shikimori",
        "screenshot_source:jikan",
        "screenshot_source:tmdb",
        "screenshot:upload",
    ]


async def test_screenshot_gallery_callback_handler_pick_is_a_noop_for_a_stale_index(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A still-successful fetch that just doesn't have the tapped index
    anymore (e.g. the gallery shrank between page loads) leaves the
    screen alone — distinct from the fetch itself failing/emptying above,
    and safe to do because the gallery message that was tapped keeps its
    own keyboard. Leaving no *trace* is the part that isn't safe: it is a
    rejected action, so it is logged as one."""
    monkeypatch.setattr(
        shikimori, "screenshots", AsyncMock(return_value=["https://shikimori.io/x/0.jpg"])
    )
    game_id = _staged_game(
        session_factory, shikimori_id=52991, setup_step=SetupStep.PICKING_SCREENSHOT
    )

    update = _make_callback_update(data="screenshot_pick:shikimori:5")
    context = _make_context(session_factory)

    with _captured_warnings() as warnings:
        await screenshot_gallery.screenshot_gallery_callback_handler(
            cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
        )

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.setup_step == SetupStep.PICKING_SCREENSHOT
    update.callback_query.edit_message_text.assert_not_awaited()
    assert any("#6" in line and "1 url(s)" in line for line in warnings)


async def test_a_tap_after_the_setup_row_is_gone_leaves_a_warning(session_factory) -> None:
    """The trigger behind most of this sub-flow's silent returns: the
    setup-abandon timer deleted the row an hour in, and every button on
    every screen it left behind is still tappable. The starter sees
    nothing happen, and production used to see nothing either, so
    diagnosing a report of it meant guessing. CLAUDE.md's table calls a
    rejected action a WARNING; there is no game row left to name, so the
    line names the starter and the payload."""
    context = _make_context(session_factory)  # no game row at all

    with _captured_warnings() as warnings:
        await screenshot_gallery.screenshot_gallery_callback_handler(
            cast(Update, _make_callback_update(data="screenshot_pick:shikimori:0")),
            cast(ContextTypes.DEFAULT_TYPE, context),
        )

    assert any("screenshot_pick:shikimori:0" in line and "1" in line for line in warnings)


async def test_screenshot_gallery_callback_handler_pick_falls_back_when_the_download_fails(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The download step (not the screenshots() listing call) failing —
    a bad URL, a network blip — must also fall back rather than
    propagate as an unhandled exception."""
    monkeypatch.setattr(
        shikimori, "screenshots", AsyncMock(return_value=["https://shikimori.io/x/0.jpg"])
    )
    game_id = _staged_game(session_factory, shikimori_id=52991)

    update = _make_callback_update(data="screenshot_pick:shikimori:0")
    context = _make_context(session_factory)
    context.bot_data["search_client"].get = AsyncMock(side_effect=httpx.ConnectError("boom"))

    await screenshot_gallery.screenshot_gallery_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.setup_step == SetupStep.PICKING_SCREENSHOT
        assert fetched.original_image is None
    update.callback_query.edit_message_text.assert_awaited_once()
    _, kwargs = update.callback_query.edit_message_text.await_args
    assert kwargs["reply_markup"] is not None


async def test_screenshot_gallery_pick_does_not_dress_a_bug_up_as_a_provider_outage(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The raw image download catches transport errors only. A
    RuntimeError out of it is a bug in our own code, not the CDN
    refusing us — reporting it to the starter as "Shikimori isn't
    responding" would hide it behind a friendly message and send them
    round the source menu for a provider that is perfectly fine. It goes
    to app's error handler with its traceback intact instead."""
    monkeypatch.setattr(
        shikimori, "screenshots", AsyncMock(return_value=["https://shikimori.io/x/0.jpg"])
    )
    _staged_game(session_factory, shikimori_id=52991)

    update = _make_callback_update(data="screenshot_pick:shikimori:0")
    context = _make_context(session_factory)
    context.bot_data["search_client"].get = AsyncMock(
        side_effect=RuntimeError("bug, not an outage")
    )

    with pytest.raises(RuntimeError):
        await screenshot_gallery.screenshot_gallery_callback_handler(
            cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
        )

    update.callback_query.edit_message_text.assert_not_awaited()


async def test_screenshot_gallery_paging_falls_back_when_telegram_rejects_the_album(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Paging's own half of the send_media_group contract: Telegram
    refusing to fetch the provider's URLs must land on the source menu,
    not escape the handler. Reached through the gallery's other button,
    so the page-drawing chokepoint is covered from both entry points."""
    urls = [f"https://shikimori.io/x/{i}.jpg" for i in range(8)]
    monkeypatch.setattr(shikimori, "screenshots", AsyncMock(return_value=urls))
    game_id = _staged_game(session_factory, shikimori_id=52991)

    update = _make_callback_update(data="screenshot_more:shikimori:5")
    context = _make_context(session_factory)
    context.bot.send_media_group = AsyncMock(side_effect=BadRequest("failed to get HTTP URL"))

    await screenshot_gallery.screenshot_gallery_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.callback_query.edit_message_text.assert_awaited_once()
    _, kwargs = update.callback_query.edit_message_text.await_args
    assert _source_callbacks(kwargs["reply_markup"]) == [
        "screenshot_source:shikimori",
        "screenshot_source:jikan",
        "screenshot_source:tmdb",
        "screenshot:upload",
    ]
    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.setup_step == SetupStep.PICKING_SCREENSHOT
        assert fetched.screenshot_picker_provider == "shikimori"


async def test_gallery_failure_keeps_a_typed_query_routable_to_the_provider(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: a same-provider gallery clears the picker (nothing is
    being resolved while it is on screen), but a failure reached *from*
    that gallery replaces it with the source menu — where a retyped
    query still has to reach this provider's cross-search, per
    _fetch_screenshots_or_fallback's docstring and MECHANICS.md's "When
    a provider fails". reply_fallback is the chokepoint every
    provider-flagged failure screen passes through, so it re-arms the
    picker."""
    monkeypatch.setattr(
        shikimori, "screenshots", AsyncMock(return_value=["https://shikimori.io/x/0.jpg"])
    )
    # Exactly the state a shown same-provider gallery leaves behind.
    game_id = _staged_game(session_factory, shikimori_id=52991, screenshot_picker_provider=None)

    update = _make_callback_update(data="screenshot_pick:shikimori:0")
    context = _make_context(session_factory)
    context.bot_data["search_client"].get = AsyncMock(side_effect=httpx.ConnectError("boom"))

    await screenshot_gallery.screenshot_gallery_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.setup_step == SetupStep.PICKING_SCREENSHOT
        assert fetched.screenshot_picker_provider == "shikimori"
        # Still nothing API-sourced backing an image — the download failed.
        assert fetched.screenshot_source is None
        assert fetched.original_image is None


async def test_more_screenshots_failure_keeps_a_typed_query_routable(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same chokepoint reached from the gallery's other button: a
    "More screenshots" page whose re-listing call fails."""
    monkeypatch.setattr(shikimori, "screenshots", AsyncMock(side_effect=RuntimeError("boom")))
    game_id = _staged_game(session_factory, shikimori_id=52991, screenshot_picker_provider=None)

    update = _make_callback_update(data="screenshot_more:shikimori:5")
    context = _make_context(session_factory)

    await screenshot_gallery.screenshot_gallery_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.screenshot_picker_provider == "shikimori"
    context.bot.send_media_group.assert_not_awaited()


async def test_screenshot_search_again_callback_handler_asks_for_a_query(session_factory) -> None:
    game_id = _staged_game(session_factory, source="anilist", anilist_id=99)
    update = _make_callback_update(data="screenshot_search_again:tmdb")
    context = _make_context(session_factory)

    await screenshot_gallery.screenshot_search_again_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        # Pure picker state — asking for a query stages no image, so
        # image provenance must stay untouched.
        assert fetched.screenshot_picker_provider == "tmdb"
        assert fetched.screenshot_source is None
    # Not the identification-search wording: here the starter is
    # re-choosing which title's screenshots to browse, not saying what
    # anime an already-uploaded image is from.
    text = update.callback_query.edit_message_text.await_args.args[0]
    assert text == i18n.t("dm_start.ask_search_screenshots", "en")


async def test_screenshot_search_again_prompt_still_offers_buttons(session_factory) -> None:
    """This prompt is where the cross-search's "None of these" now lands
    (it used to fall into identification search's retry branch), and it
    is where the gallery's "Wrong anime? Search again" has always landed.
    A typed query is only one of the three ways forward MECHANICS.md
    promises — the other two are buttons, so they have to be on screen."""
    _staged_game(session_factory, source="anilist", anilist_id=99)
    update = _make_callback_update(data="screenshot_search_again:tmdb")
    context = _make_context(session_factory)

    await screenshot_gallery.screenshot_search_again_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    _, kwargs = update.callback_query.edit_message_text.await_args
    callbacks = _source_callbacks(kwargs["reply_markup"])
    assert "screenshot_source:shikimori" in callbacks
    assert SCREENSHOT_UPLOAD_CALLBACK_DATA in callbacks
    # Nothing failed here, so no provider is flagged.
    labels = [b.text for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert not any(label.startswith("⚠️") for label in labels)


async def test_screenshot_search_again_routes_a_typed_query_after_confirming(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (#72): "Wrong anime? Search again" lives on a gallery
    message that stays tappable long after the preview moved the game to
    CONFIRMING. Arming the picker is only half the routing — until this
    tap also puts the game back on PICKING_SCREENSHOT, search.py's text
    router takes its CONFIRMING branch and drops the typed answer
    (probed live: 0 replies, 0 searches). The prompt's own buttons made
    it look answered; the reply went nowhere."""
    monkeypatch.setattr(shikimori, "search", AsyncMock(return_value=[]))
    game_id = _staged_game(
        session_factory,
        source="anilist",
        anilist_id=99,
        original_image=b"already-picked",
        screenshot_source="tmdb",
        setup_step=SetupStep.CONFIRMING,
    )
    context = _make_context(session_factory)

    await screenshot_gallery.screenshot_search_again_callback_handler(
        cast(Update, _make_callback_update(data="screenshot_search_again:shikimori")),
        cast(ContextTypes.DEFAULT_TYPE, context),
    )

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.setup_step == SetupStep.PICKING_SCREENSHOT
        assert fetched.screenshot_picker_provider == "shikimori"
        # Still pure picker state: the previously picked image and its
        # provenance survive until a new screenshot is actually chosen.
        assert fetched.screenshot_source == "tmdb"
        assert fetched.original_image == b"already-picked"

    await search.search_text_handler(
        cast(Update, _make_text_update(text="Frieren")),
        cast(ContextTypes.DEFAULT_TYPE, context),
    )

    shikimori_search = cast(AsyncMock, shikimori.search)
    shikimori_search.assert_awaited_once()
    assert shikimori_search.await_args is not None
    assert shikimori_search.await_args.args[1] == "Frieren"


async def test_the_two_stale_row_sites_in_one_handler_stay_distinguishable(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """screenshot_search_pick_callback_handler looks the row up twice, and
    both misses log through the same shared helper with the same
    `screenshot_search_pick:` payload — so the message alone cannot
    separate them and only the call site can. They are not the same event:
    the first is a mundane tap on an hour-old keyboard, the second means
    the row vanished *during* a live get_by_id round-trip, which points at
    a concurrent /stop or a racing game start. A bare logger.warning()
    inside the helper stamped the helper's own frame and made the two
    byte-identical; opt(depth=1) stamps the caller."""

    def _resolve_then_lose_the_row(*_args, **_kwargs) -> TMDBResult:
        """get_by_id succeeds, but the row is gone by the time the handler
        re-opens its session — the second site's actual trigger."""
        with session_factory() as session:
            session.query(Game).delete()
            session.commit()
        return _FRIEREN_TMDB

    monkeypatch.setattr(tmdb, "get_by_id", AsyncMock(side_effect=_resolve_then_lose_the_row))
    context = _make_context(session_factory)
    data = "screenshot_search_pick:tmdb:209867"

    with _captured_warnings("{name}:{function}:{line} - {message}") as warnings:
        # First site: nothing to find on the very first lookup.
        await screenshot_gallery.screenshot_search_pick_callback_handler(
            cast(Update, _make_callback_update(data=data)),
            cast(ContextTypes.DEFAULT_TYPE, context),
        )
        _staged_game(session_factory, source="anilist", anilist_id=99, tmdb_id=209867)
        # Second site: found, resolved, then gone before the write.
        await screenshot_gallery.screenshot_search_pick_callback_handler(
            cast(Update, _make_callback_update(data=data)),
            cast(ContextTypes.DEFAULT_TYPE, context),
        )

    assert len(warnings) == 2
    assert warnings[0] != warnings[1]
    # Not the helper's own frame, which is what made them identical.
    assert not any("_log_stale_tap" in line for line in warnings)
    assert all("screenshot_search_pick_callback_handler" in line for line in warnings)


async def test_screenshot_search_step_shows_results_with_the_cross_search_prefix(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tmdb, "search", AsyncMock(return_value=[_FRIEREN_TMDB]))
    context = _make_context(session_factory)
    status_message = MagicMock()
    status_message.edit_text = AsyncMock()
    message = MagicMock()
    message.text = "Frieren"
    message.reply_text = AsyncMock(return_value=status_message)

    await screenshot_gallery._screenshot_search_step(message, context, "en", _tmdb_menu())

    status_message.edit_text.assert_awaited_once()
    assert status_message.edit_text.await_args is not None
    _, kwargs = status_message.edit_text.await_args
    callbacks = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert "screenshot_search_pick:tmdb:209867" in callbacks
    # Pins the retry end to end, against the prefix this step really
    # builds: _retry_data_for derives the destination from pick_prefix,
    # so a change to the format string above would silently fall back to
    # identification's retry and re-open the cross-wiring.
    assert "screenshot_search_again:tmdb" in callbacks


async def test_screenshot_search_pick_callback_handler_resolves_and_shows_gallery(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tmdb, "get_by_id", AsyncMock(return_value=_FRIEREN_TMDB))
    monkeypatch.setattr(
        tmdb, "screenshots", AsyncMock(return_value=["https://image.tmdb.org/x/0.jpg"])
    )
    game_id = _staged_game(
        session_factory, source="anilist", anilist_id=99, screenshot_picker_provider="tmdb"
    )

    update = _make_callback_update(data="screenshot_search_pick:tmdb:209867")
    context = _make_context(session_factory)

    await screenshot_gallery.screenshot_search_pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.tmdb_id == 209867
        assert fetched.source == "anilist"

    context.bot.send_media_group.assert_awaited_once()
    _, msg_kwargs = context.bot.send_message.await_args
    callbacks = [b.callback_data for row in msg_kwargs["reply_markup"].inline_keyboard for b in row]
    assert "screenshot_search_again:tmdb" in callbacks
    update.callback_query.edit_message_text.assert_awaited_once()


async def test_screenshot_search_pick_callback_handler_handles_a_stale_id(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tmdb, "get_by_id", AsyncMock(return_value=None))
    _staged_game(
        session_factory, source="anilist", anilist_id=99, screenshot_picker_provider="tmdb"
    )

    update = _make_callback_update(data="screenshot_search_pick:tmdb:209867")
    context = _make_context(session_factory)

    await screenshot_gallery.screenshot_search_pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.bot.send_media_group.assert_not_awaited()
    update.callback_query.edit_message_text.assert_awaited_once()


async def test_screenshot_search_step_offers_the_source_menu_when_the_provider_is_down(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The live 2026-09-14 dead end: Jikan 504'd on every query and the
    error reply carried no buttons, so typing another query just looped.
    A failed search must come back with the source menu attached."""
    monkeypatch.setattr(tmdb, "search", AsyncMock(side_effect=RuntimeError("504")))
    context = _make_context(session_factory)
    status_message = MagicMock()
    status_message.edit_text = AsyncMock()
    message = MagicMock()
    message.text = "Shokugeki no Soma"
    message.reply_text = AsyncMock(return_value=status_message)

    await screenshot_gallery._screenshot_search_step(message, context, "en", _tmdb_menu())

    assert status_message.edit_text.await_args is not None
    args, kwargs = status_message.edit_text.await_args
    assert "TMDB" in args[0]
    assert _source_callbacks(kwargs["reply_markup"]) == [
        "screenshot_source:shikimori",
        "screenshot_source:jikan",
        "screenshot_source:tmdb",
        "screenshot:upload",
    ]
    labels = [b.text for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert any(label.startswith("⚠️") and "TMDB" in label for label in labels)


async def test_screenshot_search_step_offers_the_source_menu_when_nothing_matches(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero results was the other half of the loop — a typed query that
    matches nothing is just as much a dead end as one that errors."""
    monkeypatch.setattr(tmdb, "search", AsyncMock(return_value=[]))
    context = _make_context(session_factory)
    status_message = MagicMock()
    status_message.edit_text = AsyncMock()
    message = MagicMock()
    message.text = "zzzz"
    message.reply_text = AsyncMock(return_value=status_message)

    await screenshot_gallery._screenshot_search_step(message, context, "en", _tmdb_menu())

    assert status_message.edit_text.await_args is not None
    _, kwargs = status_message.edit_text.await_args
    assert kwargs["reply_markup"] is not None


async def test_repeated_failing_searches_each_offer_a_way_out(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bug wasn't one missing keyboard, it was that repetition never
    produced an exit — every attempt has to carry the menu, not just the
    first."""
    monkeypatch.setattr(tmdb, "search", AsyncMock(side_effect=RuntimeError("504")))
    context = _make_context(session_factory)

    for query in ("Shokugeki no Soma", "Food Wars"):
        status_message = MagicMock()
        status_message.edit_text = AsyncMock()
        message = MagicMock()
        message.text = query
        message.reply_text = AsyncMock(return_value=status_message)

        await screenshot_gallery._screenshot_search_step(message, context, "en", _tmdb_menu())

        assert status_message.edit_text.await_args is not None
        _, kwargs = status_message.edit_text.await_args
        assert kwargs["reply_markup"] is not None, f"no way out after querying {query!r}"


async def test_screenshot_search_pick_offers_the_source_menu_when_the_id_is_gone(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tmdb, "get_by_id", AsyncMock(return_value=None))
    _staged_game(session_factory, source="anilist", anilist_id=99)
    update = _make_callback_update(data="screenshot_search_pick:tmdb:209867")
    context = _make_context(session_factory)

    await screenshot_gallery.screenshot_search_pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    _, kwargs = update.callback_query.edit_message_text.await_args
    assert kwargs["reply_markup"] is not None


async def test_screenshot_gallery_callback_handler_pages_back_to_the_previous_slice(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The back button reuses the same offset-encoded callback as the
    forward one, so paging back is just another page render."""
    urls = [f"https://shikimori.io/x/{i}.jpg" for i in range(8)]
    screenshots_mock = AsyncMock(return_value=urls)
    monkeypatch.setattr(shikimori, "screenshots", screenshots_mock)
    _staged_game(session_factory, shikimori_id=52991)

    update = _make_callback_update(data="screenshot_more:shikimori:0")
    context = _make_context(session_factory)

    await screenshot_gallery.screenshot_gallery_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    _, kwargs = context.bot.send_media_group.await_args
    captions = [item.caption for item in kwargs["media"]]
    assert captions == ["1", "2", "3", "4", "5"]
    # First page again, so forward only — nothing to go back to.
    _, button_kwargs = context.bot.send_message.await_args
    callbacks = [
        b.callback_data for row in button_kwargs["reply_markup"].inline_keyboard for b in row
    ]
    assert "screenshot_more:shikimori:5" in callbacks


async def test_screenshot_gallery_second_page_offers_a_way_back(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    urls = [f"https://shikimori.io/x/{i}.jpg" for i in range(8)]
    monkeypatch.setattr(shikimori, "screenshots", AsyncMock(return_value=urls))
    _staged_game(session_factory, shikimori_id=52991)

    update = _make_callback_update(data="screenshot_more:shikimori:5")
    context = _make_context(session_factory)

    await screenshot_gallery.screenshot_gallery_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    _, button_kwargs = context.bot.send_message.await_args
    callbacks = [
        b.callback_data for row in button_kwargs["reply_markup"].inline_keyboard for b in row
    ]
    assert "screenshot_more:shikimori:0" in callbacks
    # The confirmation names the range rather than "here are some more",
    # which would be wrong when paging backwards.
    assert update.callback_query.edit_message_text.await_args is not None
    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "6" in text
    assert "8" in text


async def test_screenshot_gallery_paging_keeps_the_search_again_button(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: the "More screenshots" callback carries only a
    provider and an offset (widening it would eat into Telegram's
    64-byte callback_data budget), so page 2 used to be rendered with
    cross_provider=False and silently dropped "Wrong anime? Search
    again" — the escape hatch MECHANICS.md promises is always there, and
    paging is exactly when a starter hunting a fitting screenshot would
    reach for it."""
    urls = [f"https://image.tmdb.org/x/{i}.jpg" for i in range(8)]
    monkeypatch.setattr(tmdb, "screenshots", AsyncMock(return_value=urls))
    # The state a cross-provider resolution leaves behind: identified on
    # AniList, TMDB's id resolved by the silent cross-search, and the
    # picker pointed at TMDB so a typed correction routes there.
    game_id = _staged_game(
        session_factory,
        source="anilist",
        anilist_id=99,
        tmdb_id=209867,
        screenshot_picker_provider="tmdb",
    )

    update = _make_callback_update(data="screenshot_more:tmdb:5")
    context = _make_context(session_factory)

    await screenshot_gallery.screenshot_gallery_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    _, button_kwargs = context.bot.send_message.await_args
    callbacks = [
        b.callback_data for row in button_kwargs["reply_markup"].inline_keyboard for b in row
    ]
    assert "screenshot_search_again:tmdb" in callbacks
    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        # Paging shows another page of the same gallery, so the picker
        # keeps pointing where it did — the button stays routable.
        assert fetched.screenshot_picker_provider == "tmdb"


async def test_screenshot_gallery_paging_past_the_end_still_offers_the_source_menu(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: a stale forward tap against a list that shrank since
    the page was drawn produced two buttonless messages — a bare "no
    screenshots" notice, and the tapped gallery message edited down to a
    nonsense "Screenshots 11-10 of 2 - pick one below." with its own
    keyboard stripped off. The notice even names buttons ("pick another
    source below") that weren't there. It takes the normal failure exit
    instead: the source menu, on the message that was tapped."""
    monkeypatch.setattr(
        shikimori, "screenshots", AsyncMock(return_value=["https://shikimori.io/x/0.jpg"])
    )
    game_id = _staged_game(session_factory, shikimori_id=52991, screenshot_picker_provider=None)

    update = _make_callback_update(data="screenshot_more:shikimori:10")
    context = _make_context(session_factory)

    await screenshot_gallery.screenshot_gallery_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.bot.send_media_group.assert_not_awaited()
    update.callback_query.edit_message_text.assert_awaited_once()
    _, kwargs = update.callback_query.edit_message_text.await_args
    assert _source_callbacks(kwargs["reply_markup"]) == [
        "screenshot_source:shikimori",
        "screenshot_source:jikan",
        "screenshot_source:tmdb",
        "screenshot:upload",
    ]
    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.setup_step == SetupStep.PICKING_SCREENSHOT
        # The source menu is up, so a retyped query has to route again.
        assert fetched.screenshot_picker_provider == "shikimori"


async def test_screenshot_gallery_paging_arms_the_picker_for_a_typed_correction(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (#72): every page is drawn `cross_provider=True`, so it
    carries "Wrong anime? Search again" — and the rule
    `resume_screenshot_gallery` states in its own words is that such a
    gallery has to arm the picker, because a *typed* correction routes on
    that column alone. Paging off a same-provider gallery (picker None)
    used to draw the button while search.py silently dropped anything
    typed under it."""
    urls = [f"https://shikimori.io/x/{i}.jpg" for i in range(8)]
    monkeypatch.setattr(shikimori, "screenshots", AsyncMock(return_value=urls))
    monkeypatch.setattr(shikimori, "search", AsyncMock(return_value=[]))
    # Exactly the state a shown *same-provider* gallery leaves behind.
    game_id = _staged_game(session_factory, shikimori_id=52991, screenshot_picker_provider=None)
    context = _make_context(session_factory)

    await screenshot_gallery.screenshot_gallery_callback_handler(
        cast(Update, _make_callback_update(data="screenshot_more:shikimori:5")),
        cast(ContextTypes.DEFAULT_TYPE, context),
    )

    with session_factory() as session:
        fetched = session.get(Game, game_id)
        assert fetched is not None
        assert fetched.screenshot_picker_provider == "shikimori"
        # Pure picker state — paging stages no image.
        assert fetched.screenshot_source is None

    await search.search_text_handler(
        cast(Update, _make_text_update(text="Frieren")),
        cast(ContextTypes.DEFAULT_TYPE, context),
    )

    shikimori_search = cast(AsyncMock, shikimori.search)
    shikimori_search.assert_awaited_once()
    assert shikimori_search.await_args is not None
    assert shikimori_search.await_args.args[1] == "Frieren"


async def test_screenshot_gallery_paging_looks_the_url_list_up_once_per_page(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One `screenshots()` lookup per page render — not one per photo,
    and not a re-resolution of the provider id. The lookup itself is
    @cache.cached() in production, so stepping back and forth costs no
    real API calls; this test patches over that decorator, so the cache
    is covered by tests/services/search/test_cache.py instead."""
    urls = [f"https://shikimori.io/x/{i}.jpg" for i in range(8)]
    screenshots_mock = AsyncMock(return_value=urls)
    monkeypatch.setattr(shikimori, "screenshots", screenshots_mock)
    _staged_game(session_factory, shikimori_id=52991)
    context = _make_context(session_factory)

    for data in ("screenshot_more:shikimori:5", "screenshot_more:shikimori:0"):
        await screenshot_gallery.screenshot_gallery_callback_handler(
            cast(Update, _make_callback_update(data=data)),
            cast(ContextTypes.DEFAULT_TYPE, context),
        )

    assert screenshots_mock.await_count == 2  # one per page, not one per photo
