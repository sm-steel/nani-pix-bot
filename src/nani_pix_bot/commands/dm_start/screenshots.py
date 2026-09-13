"""Screenshot-source selection + gallery browsing — the screenshot-less
/newgame flow's own sub-flow, reached from `search.py`'s
`pick_callback_handler` once identification is staged but no image
exists yet. See MECHANICS.md's "Starting a game" section.

Every screenshot-capable provider (Shikimori/Jikan/TMDB) is always
offered, regardless of which provider did the identification: tapping
one the game already has an id for goes straight to its gallery
(same-provider path); tapping any other silently searches it by the
already-confirmed title and takes the top result (cross-provider
resolution, ticket 8) — with a "Wrong anime? Search again" button on
the resulting gallery for a correction, and the same correction is
reachable if the initial auto-search finds nothing at all."""

from dataclasses import dataclass

from loguru import logger
from telegram import InputMediaPhoto, Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start._shared import (
    _SEARCH_SERVICE_ERRORS,
    _SERVICE_DISPLAY_NAMES,
    _client_for_source,
    _show_preview,
)
from nani_pix_bot.commands.dm_start.keyboards import (
    SCREENSHOT_SEARCH_PICK_PREFIX,
    GalleryPage,
    jikan_results_keyboard,
    parse_screenshot_more_callback_data,
    parse_screenshot_pick_callback_data,
    parse_screenshot_search_again_callback_data,
    parse_screenshot_search_pick_callback_data,
    parse_screenshot_source_callback_data,
    screenshot_gallery_keyboard,
    screenshot_source_keyboard,
    shikimori_results_keyboard,
    tmdb_results_keyboard,
)
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import SetupStep
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, settings
from nani_pix_bot.services.search import jikan, shikimori, tmdb
from nani_pix_bot.services.search.jikan import JikanResult
from nani_pix_bot.services.search.shikimori import ShikimoriResult
from nani_pix_bot.services.search.tmdb import TMDBResult

GALLERY_PAGE_SIZE = 5

# One provider id column per screenshot-capable provider — see
# models/game.py's per-provider *_id columns.
_ID_ATTRS = {"shikimori": "shikimori_id", "jikan": "jikan_id", "tmdb": "tmdb_id"}
# Module references, not bound function references — a plain
# {"shikimori": shikimori.screenshots, ...} dict would capture the
# function object at import time, which stops respecting
# monkeypatch.setattr(shikimori, "screenshots", ...) in tests (and,
# more generally, would go stale if a provider module ever reassigned
# its own screenshots name after import).
_SCREENSHOT_MODULES = {"shikimori": shikimori, "jikan": jikan, "tmdb": tmdb}


async def _fetch_screenshots(provider: str, client, provider_id: int) -> list[str]:
    return await _SCREENSHOT_MODULES[provider].screenshots(client, provider_id)


async def _search_provider(
    provider: str, client, query: str
) -> list[ShikimoriResult] | list[JikanResult] | list[TMDBResult]:
    return await _SCREENSHOT_MODULES[provider].search(client, query)


async def _get_provider_by_id(
    provider: str, client, external_id: int
) -> ShikimoriResult | JikanResult | TMDBResult | None:
    return await _SCREENSHOT_MODULES[provider].get_by_id(client, external_id)


@dataclass(frozen=True)
class GalleryTarget:
    """Where a gallery page is being sent and which provider/offset
    it's paginating — bundled into one object so `_show_gallery_page`
    doesn't need a 6-argument signature (a qlty "many parameters"
    smell). `cross_provider` defaults to False since every caller in
    this ticket is same-provider; ticket 8 will pass True from its own
    cross-provider-resolution path."""

    chat_id: int
    provider: str
    offset: int
    cross_provider: bool = False


def _screenshot_capable_providers(game) -> list[str]:
    """All 3 screenshot-capable providers, same-provider-as-identification
    first when it's one of them (so the common case — screenshot source
    matches identification source — needs no cross-provider search at
    all). Every provider is offered regardless of whether the game
    already has an id for it — tapping one it doesn't triggers
    cross-provider resolution (see _resolve_screenshot_source)."""
    candidates = ["shikimori", "jikan", "tmdb"]
    if game.source in candidates:
        candidates.remove(game.source)
        candidates.insert(0, game.source)
    return candidates


async def start_screenshot_picker(context: ContextTypes.DEFAULT_TYPE, game, lang: str) -> None:
    """Entry point from `search.py`'s `pick_callback_handler` (and
    `manual.py`'s synonym step) once identification is staged with no
    image yet — shows the screenshot-source-selection keyboard. Every
    screenshot-capable provider is always offered (see
    _screenshot_capable_providers) since cross-provider resolution
    means even an AniList/manual identification can still get a
    Shikimori/Jikan/TMDB screenshot."""
    providers = _screenshot_capable_providers(game)
    game.setup_step = SetupStep.PICKING_SCREENSHOT
    await context.bot.send_message(
        chat_id=game.starter_id,
        text=i18n.t("dm_start.pick_screenshot_source_prompt", lang),
        reply_markup=screenshot_source_keyboard(providers, lang),
    )


def clear_screenshot_selection(game) -> None:
    """Drops an API-picked screenshot and the provider id that resolved
    it — used by preview.py's "Re-search title" when the current image
    is API-sourced, since a re-search that picks a different anime
    shouldn't leave the old anime's screenshot attached to it. A no-op
    if the current image is a genuine upload (screenshot_source is
    None) — that one is preserved across a re-search, unchanged from
    before ticket 9."""
    if game.screenshot_source is None:
        return
    setattr(game, _ID_ATTRS[game.screenshot_source], None)
    game.original_image = None
    game.screenshot_source = None


async def resume_screenshot_gallery(context: ContextTypes.DEFAULT_TYPE, game, lang: str) -> None:
    """Re-shows `game.screenshot_source`'s gallery from the top using its
    already-resolved id — preview.py's "Change image" -> "Pick a
    different screenshot" branch, reached only when the current image
    is API-sourced (ticket 9). `cross_provider=True` unconditionally so
    "Wrong anime? Search again" is always offered here, letting the
    starter back out to a different provider entirely if nothing in
    this one's gallery fits."""
    provider = game.screenshot_source
    assert provider is not None, "resume_screenshot_gallery called with no screenshot_source"
    provider_id = getattr(game, _ID_ATTRS[provider])
    client = _client_for_source(context, provider)
    urls = await _fetch_screenshots(provider, client, provider_id)
    target = GalleryTarget(
        chat_id=game.starter_id, provider=provider, offset=0, cross_provider=True
    )
    await _show_gallery_page(context, target, urls, lang)


async def screenshot_upload_instead_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """ "Upload my own instead" — offered on both the source-selection
    keyboard and the gallery keyboard, but registered as its own
    handler (rather than duplicated in each of theirs) since the
    behavior is identical regardless of which screen it was tapped
    from: fall back to the traditional upload flow, reusing the
    existing "Change image" photo-replacement handling."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    user = query.from_user
    if user is None:
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        game = game_service.get_setup_game_for_starter(session, user.id)
        if game is None:
            return
        game.setup_step = SetupStep.AWAITING_PHOTO_CHANGE
        await query.edit_message_text(i18n.t("dm_start.ask_new_photo", lang))


async def screenshot_source_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """A screenshot-source button tapped from `start_screenshot_picker`'s
    keyboard — same-provider (an id already on file) or cross-provider
    (ticket 8's silent auto-search), see _resolve_screenshot_source."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()
    user = query.from_user
    if user is None:
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        game = game_service.get_setup_game_for_starter(session, user.id)
        if game is None:
            return

        provider = parse_screenshot_source_callback_data(query.data)
        if provider is None:
            return
        game.screenshot_source = provider
        reply_key, reply_kwargs = await _resolve_screenshot_source(context, game, provider, lang)

    await query.edit_message_text(i18n.t(reply_key, lang, **reply_kwargs))


async def _resolve_screenshot_source(
    context: ContextTypes.DEFAULT_TYPE, game, provider: str, lang: str
) -> tuple[str, dict]:
    """Runs once a screenshot-source button is tapped: same-provider (an
    id already on file) goes straight to the gallery; cross-provider
    silently searches by the confirmed title first (ticket 8) and only
    falls back to asking for a manual query if that search finds
    nothing. Returns the i18n key (+ format kwargs) for the caller's
    own follow-up message edit."""
    provider_id = getattr(game, _ID_ATTRS[provider])
    cross_provider = provider_id is None
    if cross_provider:
        provider_id = await _resolve_cross_provider_id(context, game, provider)
        if provider_id is None:
            return "dm_start.cross_provider_search_failed", {
                "service": _SERVICE_DISPLAY_NAMES[provider]
            }

    client = _client_for_source(context, provider)
    urls = await _fetch_screenshots(provider, client, provider_id)
    logger.debug("Game {}: fetched {} {} screenshot(s)", game.id, len(urls), provider)
    target = GalleryTarget(
        chat_id=game.starter_id, provider=provider, offset=0, cross_provider=cross_provider
    )
    await _show_gallery_page(context, target, urls, lang)
    return "dm_start.screenshot_source_picked", {}


async def _resolve_cross_provider_id(
    context: ContextTypes.DEFAULT_TYPE, game, provider: str
) -> int | None:
    """Silently searches `provider` by the already-confirmed title and
    stages its top result's id onto the game
    (game_service.set_screenshot_provider_id) — ticket 8's cross-
    provider resolution. Returns None on zero results or a search
    failure so the caller can fall back to asking for a manual query
    (the "Wrong anime? Search again" flow lands in that exact same
    fallback state, see search_text_handler's PICKING_SCREENSHOT
    branch in search.py)."""
    query_text = game.title_english or game.title_romaji or ""
    client = _client_for_source(context, provider)
    try:
        results = await _search_provider(provider, client, query_text)
    except _SEARCH_SERVICE_ERRORS:
        logger.exception(
            "Game {}: {} cross-provider screenshot search failed for {!r}",
            game.id,
            provider,
            query_text,
        )
        return None

    if not results:
        logger.info("Game {}: no {} cross-provider match for {!r}", game.id, provider, query_text)
        return None

    game_service.set_screenshot_provider_id(game, results[0])
    provider_id = getattr(game, _ID_ATTRS[provider])
    logger.info("Game {}: cross-provider resolved {} -> id {}", game.id, provider, provider_id)
    return provider_id


async def screenshot_search_again_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """ "Wrong anime? Search again" — tapped from a cross-provider
    gallery to correct a bad auto-resolved top result. Just asks for a
    query; screenshot_source (already set to `provider` by whichever
    step showed this button) is what tells search_text_handler to route
    the next text message to _screenshot_search_step below."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()
    user = query.from_user
    if user is None:
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        game = game_service.get_setup_game_for_starter(session, user.id)
        if game is None:
            return
        provider = parse_screenshot_search_again_callback_data(query.data)
        if provider is None:
            return
        game.screenshot_source = provider

    await query.edit_message_text(i18n.t("dm_start.ask_search", lang))


async def _screenshot_search_step(
    message, context: ContextTypes.DEFAULT_TYPE, lang: str, provider: str
) -> None:
    """A search query typed while resolving `provider`'s screenshot (the
    "Wrong anime? Search again" correction, or the fallback state after
    an auto-search found nothing) — mirrors search.py's own
    `_search_step`, but wires its results keyboard to
    screenshot_search_pick_callback_handler instead of identification
    search's own pick_callback_handler (see keyboards.py's `pick_prefix`
    override on the shared *_results_keyboard builders)."""
    status_message = await message.reply_text(i18n.t("dm_start.searching", lang))
    logger.debug("{} screenshot cross-search started for query {!r}", provider, message.text)

    client = _client_for_source(context, provider)
    pick_prefix = f"{SCREENSHOT_SEARCH_PICK_PREFIX}{provider}:"
    try:
        # Dispatched inline (rather than through a provider->function
        # dict, like _fetch_screenshots/_search_provider use) since each
        # branch's *_results_keyboard builder needs its own specific
        # result type — a dict of them collapses to a union ty can't
        # narrow back down per call. Mirrors search.py's own
        # _search_step for the same reason.
        if provider == "shikimori":
            results = await shikimori.search(client, message.text)
            keyboard = shikimori_results_keyboard(results, lang, pick_prefix=pick_prefix)
        elif provider == "jikan":
            results = await jikan.search(client, message.text)
            keyboard = jikan_results_keyboard(results, lang, pick_prefix=pick_prefix)
        else:
            results = await tmdb.search(client, message.text)
            keyboard = tmdb_results_keyboard(results, lang, pick_prefix=pick_prefix)
    except _SEARCH_SERVICE_ERRORS:
        logger.exception("{} screenshot cross-search failed for query {!r}", provider, message.text)
        await status_message.edit_text(
            i18n.t(
                "dm_start.cross_provider_search_failed",
                lang,
                service=_SERVICE_DISPLAY_NAMES[provider],
            )
        )
        return

    logger.debug(
        "{} screenshot cross-search for {!r} returned {} result(s)",
        provider,
        message.text,
        len(results),
    )
    if not results:
        await status_message.edit_text(i18n.t("dm_start.no_results", lang))
        return

    await status_message.edit_text(i18n.t("dm_start.pick_prompt", lang), reply_markup=keyboard)


async def screenshot_search_pick_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """A result tapped from `_screenshot_search_step`'s keyboard —
    resolves it, stages its id (game_service.set_screenshot_provider_id,
    never stage_result — this is a screenshot correction, not a
    re-identification) and shows its gallery."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()
    user = query.from_user
    if user is None:
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)

    resolved = await _resolve_screenshot_search_pick(query, context, lang)
    if resolved is None:
        return
    provider, external_id, result = resolved

    with session_scope(session_factory) as session:
        game = game_service.get_setup_game_for_starter(session, user.id)
        if game is None:
            return
        game_service.set_screenshot_provider_id(game, result)
        logger.debug(
            "Game {}: cross-provider search resolved {} -> id {}", game.id, provider, external_id
        )
        client = _client_for_source(context, provider)
        urls = await _fetch_screenshots(provider, client, external_id)
        target = GalleryTarget(
            chat_id=game.starter_id, provider=provider, offset=0, cross_provider=True
        )
        await _show_gallery_page(context, target, urls, lang)

    await query.edit_message_text(i18n.t("dm_start.screenshot_source_picked", lang))


async def _resolve_screenshot_search_pick(
    query, context: ContextTypes.DEFAULT_TYPE, lang: str
) -> tuple[str, int, ShikimoriResult | JikanResult | TMDBResult] | None:
    """Parses the pick and re-fetches the full result via get_by_id
    (restart-resilient, same reasoning as search.py's own
    `_resolve_picked_result`). Replies and returns None for every
    already-handled outcome: unparseable callback data, the search
    service erroring, or the id no longer existing."""
    parsed = parse_screenshot_search_pick_callback_data(query.data)
    if parsed is None:
        return None
    provider, external_id = parsed

    client = _client_for_source(context, provider)
    try:
        result = await _get_provider_by_id(provider, client, external_id)
    except _SEARCH_SERVICE_ERRORS:
        logger.exception("{} get_by_id failed for id {}", provider, external_id)
        await query.edit_message_text(
            i18n.t(
                "dm_start.cross_provider_search_failed",
                lang,
                service=_SERVICE_DISPLAY_NAMES[provider],
            )
        )
        return None

    if result is None:
        logger.warning("{} id {} picked but no longer found", provider, external_id)
        await query.edit_message_text(i18n.t("dm_start.not_found_anymore", lang))
        return None

    return provider, external_id, result


async def screenshot_gallery_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """A numbered screenshot or "More screenshots" button tapped from
    the gallery's own keyboard."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()
    user = query.from_user
    if user is None:
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        game = game_service.get_setup_game_for_starter(session, user.id)
        if game is None:
            return
        reply_key = await _dispatch_gallery_action(context, session, game, query.data, lang)

    if reply_key is not None:
        await query.edit_message_text(i18n.t(reply_key, lang))


async def _dispatch_gallery_action(
    context: ContextTypes.DEFAULT_TYPE, session, game, data: str, lang: str
) -> str | None:
    """Runs the gallery action `data` encodes (a "More screenshots" page
    or a numbered pick) and returns the i18n key for the resulting
    message, or None for a stale-button no-op — split out of
    `screenshot_gallery_callback_handler` itself to keep that handler's
    own return count under qlty's "many returns" threshold."""
    more = parse_screenshot_more_callback_data(data)
    if more is not None:
        await _handle_more_screenshots(context, game, more, lang)
        return "dm_start.more_screenshots_sent"

    picked = parse_screenshot_pick_callback_data(data)
    if picked is None:
        return None
    picked_ok = await _handle_screenshot_pick(context, session, game, picked, lang)
    # picked_ok is False for a stale button (e.g. a re-fetch returned
    # fewer results than the tapped index) — a no-op, same as `picked
    # is None` above.
    return "dm_start.preview_sent" if picked_ok else None


async def _handle_more_screenshots(
    context: ContextTypes.DEFAULT_TYPE, game, more: tuple[str, int], lang: str
) -> None:
    provider, offset = more
    provider_id = getattr(game, _ID_ATTRS[provider])
    client = _client_for_source(context, provider)
    urls = await _fetch_screenshots(provider, client, provider_id)
    target = GalleryTarget(chat_id=game.starter_id, provider=provider, offset=offset)
    await _show_gallery_page(context, target, urls, lang)


async def _handle_screenshot_pick(
    context: ContextTypes.DEFAULT_TYPE, session, game, picked: tuple[str, int], lang: str
) -> bool:
    """Downloads the picked screenshot's bytes and shows the
    confirmation preview. Returns False (a no-op, caller shouldn't edit
    the triggering message) if the index is now out of range."""
    provider, index = picked
    provider_id = getattr(game, _ID_ATTRS[provider])
    client = _client_for_source(context, provider)
    urls = await _fetch_screenshots(provider, client, provider_id)
    if index >= len(urls):
        return False

    # These are external URLs (Shikimori/Jikan/TMDB), not Telegram
    # file_ids — the gallery album itself lets Telegram fetch them
    # server-side, but storing one as original_image needs the actual
    # bytes downloaded ourselves.
    download_client = context.bot_data["search_client"]
    response = await download_client.get(urls[index])
    response.raise_for_status()
    game.original_image = response.content
    game.screenshot_source = provider
    logger.debug("Game {}: picked {} screenshot #{}", game.id, provider, index + 1)

    await _show_preview(context, session, game, lang)
    return True


async def _show_gallery_page(
    context: ContextTypes.DEFAULT_TYPE, target: GalleryTarget, urls: list[str], lang: str
) -> None:
    """Sends an album of up to GALLERY_PAGE_SIZE numbered screenshots
    starting at `target.offset`, followed by the gallery's own buttons
    message. Telegram fetches media-group photos server-side from a URL
    directly — no need to download bytes ourselves until one is
    actually picked."""
    shown = urls[target.offset : target.offset + GALLERY_PAGE_SIZE]
    if not shown:
        await context.bot.send_message(
            chat_id=target.chat_id, text=i18n.t("dm_start.no_screenshots_available", lang)
        )
        return

    media = [
        InputMediaPhoto(media=url, caption=str(target.offset + i + 1))
        for i, url in enumerate(shown)
    ]
    await context.bot.send_media_group(chat_id=target.chat_id, media=media)

    has_more = len(urls) > target.offset + len(shown)
    page = GalleryPage(
        provider=target.provider,
        offset=target.offset,
        count=len(shown),
        has_more=has_more,
        cross_provider=target.cross_provider,
    )
    await context.bot.send_message(
        chat_id=target.chat_id,
        text=i18n.t("dm_start.pick_screenshot_prompt", lang),
        reply_markup=screenshot_gallery_keyboard(page, lang),
    )
