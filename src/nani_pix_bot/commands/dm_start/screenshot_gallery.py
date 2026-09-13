"""Browsing an already-shown screenshot gallery — numbered picks, "More
screenshots", downloading the chosen bytes, and the "Wrong anime?
Search again" cross-provider correction sub-flow. Split out of
screenshots.py (which owns source *selection* — the source-selection
keyboard and same-/cross-provider resolution up to the point a gallery
is first shown) once that file's total complexity grew past qlty's
threshold; see MECHANICS.md's "Starting a game" section for the
player-facing flow both files implement together."""

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start._shared import (
    _SEARCH_SERVICE_ERRORS,
    _SERVICE_DISPLAY_NAMES,
    _client_for_source,
    _show_preview,
)
from nani_pix_bot.commands.dm_start.keyboards import (
    SCREENSHOT_SEARCH_PICK_PREFIX,
    jikan_results_keyboard,
    parse_screenshot_more_callback_data,
    parse_screenshot_pick_callback_data,
    parse_screenshot_search_again_callback_data,
    parse_screenshot_search_pick_callback_data,
    shikimori_results_keyboard,
    tmdb_results_keyboard,
)
from nani_pix_bot.commands.dm_start.screenshots import (
    GalleryTarget,
    _fetch_screenshots_or_fallback,
    _get_provider_by_id,
    _provider_id,
    _show_gallery_page,
)
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import SetupStep
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, settings
from nani_pix_bot.services.search import jikan, shikimori, tmdb
from nani_pix_bot.services.search.jikan import JikanResult
from nani_pix_bot.services.search.shikimori import ShikimoriResult
from nani_pix_bot.services.search.tmdb import TMDBResult


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
        fetched = await _fetch_screenshots_or_fallback(context, game, provider, external_id)
        if isinstance(fetched, str):
            reply_key = fetched
        else:
            target = GalleryTarget(
                chat_id=game.starter_id, provider=provider, offset=0, cross_provider=True
            )
            await _show_gallery_page(context, target, fetched, lang)
            reply_key = "dm_start.screenshot_source_picked"

    await query.edit_message_text(i18n.t(reply_key, lang))


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
        return await _handle_more_screenshots(context, game, more, lang)

    picked = parse_screenshot_pick_callback_data(data)
    if picked is None:
        return None
    return await _handle_screenshot_pick(context, session, game, picked, lang)


async def _handle_more_screenshots(
    context: ContextTypes.DEFAULT_TYPE, game, more: tuple[str, int], lang: str
) -> str | None:
    provider, offset = more
    provider_id = _provider_id(game, provider)
    result = await _fetch_screenshots_or_fallback(context, game, provider, provider_id)
    if isinstance(result, str):
        return result
    target = GalleryTarget(chat_id=game.starter_id, provider=provider, offset=offset)
    await _show_gallery_page(context, target, result, lang)
    return "dm_start.more_screenshots_sent"


async def _handle_screenshot_pick(
    context: ContextTypes.DEFAULT_TYPE, session, game, picked: tuple[str, int], lang: str
) -> str | None:
    """Downloads the picked screenshot's bytes and shows the
    confirmation preview. Returns the i18n key for the caller's own
    follow-up message edit, or None for a stale-button no-op (the
    tapped index is out of range against a still-successful fetch —
    distinct from a fetch failure/empty result, which
    _fetch_screenshots_or_fallback already turns into its own fallback
    key and setup_step transition)."""
    provider, index = picked
    provider_id = _provider_id(game, provider)
    result = await _fetch_screenshots_or_fallback(context, game, provider, provider_id)
    if isinstance(result, str):
        return result
    urls = result
    if index >= len(urls):
        return None

    # These are external URLs (Shikimori/Jikan/TMDB), not Telegram
    # file_ids — the gallery album itself lets Telegram fetch them
    # server-side, but storing one as original_image needs the actual
    # bytes downloaded ourselves. Routed through _client_for_source
    # (not the bare search_client) so a TMDB pick downloads through the
    # same proxied, Bearer-authed client its search/screenshots calls
    # already use — TMDB's image CDN may be behind the same DNS block
    # as api.themoviedb.org (see ARCHITECTURE.md's connectivity notes).
    download_client = _client_for_source(context, provider)
    try:
        response = await download_client.get(urls[index])
        response.raise_for_status()
    except _SEARCH_SERVICE_ERRORS:
        logger.exception(
            "Game {}: downloading {} screenshot #{} failed", game.id, provider, index + 1
        )
        game.setup_step = SetupStep.AWAITING_PHOTO_CHANGE
        return "dm_start.screenshot_fetch_failed"

    game.original_image = response.content
    game.screenshot_source = provider
    logger.debug("Game {}: picked {} screenshot #{}", game.id, provider, index + 1)

    await _show_preview(context, session, game, lang)
    return "dm_start.preview_sent"
