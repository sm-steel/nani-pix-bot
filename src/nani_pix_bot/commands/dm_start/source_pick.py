"""The screenshot-source button tap (issue #292's split of screenshots.py):
same-provider (an id already on file) or cross-provider (ticket 8's silent
auto-search by the confirmed title). It runs in phases so that no Telegram
call or provider round-trip ever happens inside an open write transaction:
read the tap; answer it and search/fetch with no session open; write what
that resolved in a short session; then send the gallery or the fallback."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start._shared import (
    SEARCH_SERVICE_ERRORS,
    _reject_stale_tap,
    client_for_source,
)
from nani_pix_bot.commands.dm_start.keyboards import parse_screenshot_source_callback_data
from nani_pix_bot.commands.dm_start.screenshots import (
    FallbackNotice,
    GalleryTarget,
    ScreenshotFailure,
    _fetch_screenshots_or_fallback,
    _provider_id,
    _search_provider,
    _show_gallery_page,
    send_fallback_notice,
    stage_fallback,
)
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import Provider
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, settings
from nani_pix_bot.services.search.shikimori import ShikimoriResult
from nani_pix_bot.services.search.tenrai import TenraiResult
from nani_pix_bot.services.search.tmdb import TMDBResult


async def screenshot_source_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """A screenshot-source button tapped from `start_screenshot_picker`'s
    keyboard — same-provider (an id already on file) or cross-provider
    (ticket 8's silent auto-search).

    In phases, so no Telegram call or provider round-trip ever runs inside
    an open write transaction (issues #82, #292): read the tap, answer it
    and search/fetch with no session open, then write what that resolved
    (the cross-provider id, the picker column, or a fallback) in a short
    session, and only then send the gallery or the fallback screen. A
    gallery send that fails afterwards stages its fallback in one more
    short session."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    user = query.from_user
    if user is None:
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        game = game_service.get_setup_game_for_starter(session, user.id)
        stale = game is None
        pick = None if game is None else _pick_from_tap(game, query.data)
    if stale:
        await _reject_stale_tap(query, lang)
        return
    # One answer per query id, so this waits until the stale branch above
    # has had its chance at it (see _reject_stale_tap); still ahead of the
    # provider round-trip, so the spinner clears as soon as the tap is known
    # to be good.
    await query.answer()
    if pick is None:
        return
    fetched = await _fetch_for_pick(context, pick)
    staged = _stage_fetched(session_factory, pick, fetched)
    if isinstance(staged, GalleryTarget) and isinstance(fetched.result, list):
        failure = await _show_gallery_page(context, staged, fetched.result, lang)
        if failure is None:
            await query.edit_message_text(i18n.t("dm_start.screenshot_source_picked", lang))
            return
        staged = _stage_on_setup(session_factory, pick, lambda game: stage_fallback(game, failure))
    if isinstance(staged, FallbackNotice):
        await send_fallback_notice(query.edit_message_text, staged, lang)


_Staged = TypeVar("_Staged")


@dataclass(frozen=True)
class _SourcePick:
    """A screenshot-source tap, captured from the game inside the first
    session so the provider round-trips can run after it has closed."""

    game_id: int
    starter_id: int
    provider: Provider
    provider_id: int | None  # None: not identified there yet, search by title
    query_text: str


@dataclass(frozen=True)
class _Fetched:
    """What the provider round-trips found: the cross-provider top result
    to record (None for a same-provider pick, or a search that found
    nothing), and the screenshots or the failure to show instead."""

    found: ShikimoriResult | TenraiResult | TMDBResult | None
    result: list[str] | ScreenshotFailure


def _pick_from_tap(game: Game, data: str) -> _SourcePick | None:
    provider = parse_screenshot_source_callback_data(data)
    if provider is None:
        # keyboards.py already logged what was wrong with the payload
        # itself; this says which screen it was aimed at and which game it
        # would have moved, neither of which it can see.
        logger.warning("rejected screenshot-source tap {data!r}", data=data, game_id=game.id)
        return None
    logger.info("picked {provider} as the screenshot source", provider=provider, game_id=game.id)
    return _SourcePick(
        game_id=game.id,
        starter_id=game.starter_id,
        provider=provider,
        provider_id=_provider_id(game, provider),
        query_text=_cross_provider_query(game),
    )


def _cross_provider_query(game: Game) -> str:
    """The title a cross-provider search asks for.

    Every stored variant, not just the Latin-script two: AniList often
    has no English title and TMDB has no romaji one at all, so a game can
    easily reach here identified by its native (or, from Shikimori, its
    Russian) title alone — which used to be searched for as `""`.

    Same order as prioritized_title()'s non-RU one (services/game/
    state.py) — a restatement of it, tied to that function by intent but
    not by code, so a reorder there wants a look here. Deliberately not
    display_title() itself: a *search query* must not follow the bot's
    display language (a RU bot would then send Tenrai/TMDB a Russian
    title even when an English one is on file), and display_title's "?"
    fallback for a title-less game would be a query rather than the
    no-query this still wants. Native before Russian for the same reason
    — all three providers index the Japanese title, only Shikimori knows
    the Russian one."""
    titles = (game.title_english, game.title_romaji, game.title_native, game.title_russian)
    return next((title for title in titles if title), "")


async def _search_cross_provider(
    context: ContextTypes.DEFAULT_TYPE, pick: _SourcePick
) -> ShikimoriResult | TenraiResult | TMDBResult | None:
    """Ticket 8's silent auto-search: the provider's top result for the
    already-confirmed title, or None on zero results or a failure — the
    caller then falls back to asking for a manual query (the same state
    "Wrong anime? Search again" lands in, see search.py)."""
    client = client_for_source(context, pick.provider)
    try:
        results = await _search_provider(pick.provider, client, pick.query_text)
    except SEARCH_SERVICE_ERRORS:
        logger.exception(
            "{provider} cross-provider screenshot search failed for {query!r}",
            provider=pick.provider,
            query=pick.query_text,
            game_id=pick.game_id,
        )
        return None
    if not results:
        logger.info(
            "no {provider} cross-provider match for {query!r}",
            provider=pick.provider,
            query=pick.query_text,
            game_id=pick.game_id,
        )
        return None
    return results[0]


async def _fetch_for_pick(context: ContextTypes.DEFAULT_TYPE, pick: _SourcePick) -> _Fetched:
    """The network phase, no session open: a cross-provider search when no
    id is on file yet, then the screenshot fetch."""
    if pick.provider_id is not None:
        result = await _fetch_screenshots_or_fallback(
            context, pick.game_id, pick.provider, pick.provider_id
        )
        return _Fetched(None, result)
    found = await _search_cross_provider(context, pick)
    if found is None:
        return _Fetched(
            None, ScreenshotFailure("dm_start.cross_provider_search_failed", pick.provider)
        )
    provider_id: int = getattr(found, pick.provider.id_attr_name)
    logger.info(
        "cross-provider resolved {provider} -> id {provider_id}",
        provider=pick.provider,
        provider_id=provider_id,
        game_id=pick.game_id,
    )
    result = await _fetch_screenshots_or_fallback(context, pick.game_id, pick.provider, provider_id)
    return _Fetched(found, result)


def _stage_on_setup(
    session_factory, pick: _SourcePick, stage: Callable[[Game], _Staged]
) -> _Staged | None:
    """Runs `stage` on the tapped game in a fresh short session, or returns
    None (logged) if that setup ended while the network phase ran."""
    with session_scope(session_factory) as session:
        game = game_service.get_setup_game_for_starter(session, pick.starter_id)
        if game is None or game.id != pick.game_id:
            logger.warning(
                "setup ended while its {provider} screenshots were loading — dropping the pick",
                provider=pick.provider,
                game_id=pick.game_id,
            )
            return None
        return stage(game)


def _stage_fetched(
    session_factory, pick: _SourcePick, fetched: _Fetched
) -> GalleryTarget | FallbackNotice | None:
    """Writes what the network phase resolved, then says what to send: the
    gallery, a fallback screen, or nothing when the setup is gone."""

    def stage(game: Game) -> GalleryTarget | FallbackNotice:
        if fetched.found is not None:
            game_service.set_screenshot_provider_id(game, fetched.found)
        if isinstance(fetched.result, ScreenshotFailure):
            return stage_fallback(game, fetched.result)
        cross_provider = pick.provider_id is None
        logger.info(
            "showing the {provider} gallery ({count} screenshot(s), cross-provider: "
            "{cross_provider})",
            provider=pick.provider,
            count=len(fetched.result),
            cross_provider=cross_provider,
            game_id=game.id,
        )
        # A cross-provider gallery carries "Wrong anime? Search again", so a
        # typed correction still has to reach this provider's search. A
        # same-provider one has nothing left to resolve — the id came from
        # identification, and it offers no such button — so a typed message
        # there is not a correction query and must not be searched as one.
        # Never screenshot_source: no image exists yet (see models/game.py).
        game.screenshot_picker_provider = pick.provider if cross_provider else None
        return GalleryTarget(
            chat_id=pick.starter_id,
            provider=pick.provider,
            offset=0,
            cross_provider=cross_provider,
        )

    return _stage_on_setup(session_factory, pick, stage)
