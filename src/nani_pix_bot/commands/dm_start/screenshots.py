"""Screenshot-source selection — the screenshot-less /newgame flow's own
sub-flow, reached from `search.py`'s `pick_callback_handler` once
identification is staged but no image exists yet. See MECHANICS.md's
"Starting a game" section.

Every screenshot-capable provider (Shikimori/Jikan/TMDB) is always
offered, regardless of which provider did the identification: tapping
one the game already has an id for goes straight to its gallery
(same-provider path); tapping any other silently searches it by the
already-confirmed title and takes the top result (cross-provider
resolution, ticket 8) — with a "Wrong anime? Search again" button on
the resulting gallery for a correction, and the same correction is
reachable if the initial auto-search finds nothing at all.

Browsing an already-shown gallery (numbered picks, "More screenshots",
the "Wrong anime? Search again" correction sub-flow) lives in
screenshot_gallery.py instead — split out once this file's own total
complexity grew past qlty's threshold; that module imports the
`_fetch_screenshots_or_fallback`/`_provider_id`/`GalleryTarget`/
`_show_gallery_page` helpers below rather than duplicating them."""

from dataclasses import dataclass

from loguru import logger
from telegram import InputMediaPhoto, Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start._shared import (
    _SEARCH_SERVICE_ERRORS,
    _SERVICE_DISPLAY_NAMES,
    _client_for_source,
)
from nani_pix_bot.commands.dm_start.keyboards import (
    GalleryPage,
    parse_screenshot_source_callback_data,
    screenshot_gallery_keyboard,
    screenshot_source_keyboard,
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


@dataclass(frozen=True)
class SourceMenu:
    """The source-selection menu, plus which provider is currently in
    play. Bundled into one object so the failure helpers below stay
    under qlty's parameter threshold (same reasoning as GalleryTarget
    above), and so `_screenshot_search_step` — which runs after its
    caller's session has closed and so can't look the game up itself —
    can be handed everything it needs to draw the menu in one value."""

    providers: list[str]
    # None only for the one failure with no provider to blame: a stale
    # "pick a different screenshot" button on a game whose
    # screenshot_source was cleared meanwhile. Nothing gets flagged and
    # the message carries no {service}.
    provider: str | None


@dataclass(frozen=True)
class Fallback:
    """A screenshot-sub-flow failure, propagated up to whichever handler
    owns the reply: the message to show and the provider to blame. Every
    one of these ends at `reply_with_source_menu` — carrying the provider
    along means no caller has to remember which one it was asking about."""

    key: str
    provider: str


def _provider_id(game, provider: str) -> int | None:
    """Typed wrapper around the getattr(game, _ID_ATTRS[provider]) dance
    used throughout this module — a bare getattr on a dynamic attribute
    name is `Any` to `ty`, which silently let a stale/cleared id (e.g.
    after clear_screenshot_selection) flow into a screenshot fetch as if
    it were always a real int. Routing every read through here means a
    caller has to explicitly deal with the `None` case."""
    return getattr(game, _ID_ATTRS[provider])


async def _fetch_screenshots(provider: str, client, provider_id: int) -> list[str]:
    return await _SCREENSHOT_MODULES[provider].screenshots(client, provider_id)


async def _fetch_screenshots_or_fallback(
    context: ContextTypes.DEFAULT_TYPE, game, provider: str, provider_id: int | None
) -> list[str] | Fallback:
    """Fetches `provider`'s screenshots for `provider_id` — the one
    chokepoint every screenshot-gallery call site routes through.
    Returns the url list on success (which can still legitimately be
    empty). On a stale/missing id, a fetch failure, or a genuinely empty
    result, returns the i18n key for a fallback message instead —
    distinguishing "couldn't reach the service" from "this one just has
    no screenshots" so the starter gets an accurate reason — which every
    caller hands to `reply_with_source_menu`.

    `setup_step` stays at PICKING_SCREENSHOT rather than moving to
    AWAITING_PHOTO_CHANGE: the starter is going back to the source menu,
    where typing a new query still has to route to the cross-provider
    search. Sending a plain photo works from here too — intake.py
    accepts one throughout PICKING_SCREENSHOT, which is what lets this
    step mean "which screen am I on" without also having to mean "may I
    accept a photo"."""
    if provider_id is None:
        logger.warning(
            "Game {}: no {} id on file for a screenshot fetch (stale button?)", game.id, provider
        )
        return Fallback("dm_start.no_screenshots_available", provider)

    client = _client_for_source(context, provider)
    try:
        urls = await _fetch_screenshots(provider, client, provider_id)
    except _SEARCH_SERVICE_ERRORS:
        logger.exception(
            "Game {}: fetching {} screenshots failed for id {}", game.id, provider, provider_id
        )
        return Fallback("dm_start.screenshot_service_down", provider)

    if not urls:
        logger.info("Game {}: {} has no screenshots for id {}", game.id, provider, provider_id)
        return Fallback("dm_start.no_screenshots_available", provider)

    return urls


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


def source_menu_for(game, provider: str | None) -> SourceMenu:
    return SourceMenu(providers=_screenshot_capable_providers(game), provider=provider)


async def reply_fallback(send, game, lang: str, fallback: Fallback) -> None:
    """`reply_with_source_menu` for the common case where the caller has
    the game loaded and so can build the menu itself.

    Puts the game back on PICKING_SCREENSHOT, since that is literally
    the screen being shown — whatever step the failure interrupted (a
    gallery pick, a "More screenshots" page, the preview's "pick a
    different screenshot"), the starter is now looking at the
    source-selection menu again and a typed query has to route to the
    cross-provider search."""
    game.setup_step = SetupStep.PICKING_SCREENSHOT
    await reply_with_source_menu(send, source_menu_for(game, fallback.provider), lang, fallback.key)


async def reply_with_source_menu(send, menu: SourceMenu, lang: str, key: str) -> None:
    """The one exit every screenshot-sub-flow failure takes: say what
    went wrong, and put the starter back on the source-selection menu
    with the offending provider flagged.

    The screenshot sub-flow shipped without this (see MECHANICS.md's
    "Starting a game") and every failure reply went out with no buttons
    at all, leaving a SETUP game whose only escapes were `/stop` or the
    1-hour setup-abandon timer — a provider outage put the starter in a
    literal loop: type a query, get an error, repeat. This mirrors
    `_shared.py::_reply_service_down`, which has always done exactly
    this for the *identification* search, down to taking the `send`
    callable so one helper serves `query.edit_message_text`,
    `status_message.edit_text` and `context.bot.send_message` alike.

    Every fallback message names the provider, so `key` must be one of
    the `{service}` strings — except when there is no provider to blame
    (see SourceMenu.provider), where `key` must be a placeholder-free
    one, since i18n.t's `.format` raises on a missing kwarg."""
    logger.debug("Screenshot flow: falling back to the source menu with {!r}", key)
    service = {"service": _SERVICE_DISPLAY_NAMES[menu.provider]} if menu.provider else {}
    await send(
        i18n.t(key, lang, **service),
        reply_markup=screenshot_source_keyboard(
            menu.providers, lang, failed_provider=menu.provider
        ),
    )


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


async def resume_screenshot_gallery(
    context: ContextTypes.DEFAULT_TYPE, game, lang: str
) -> str | Fallback:
    """Re-shows `game.screenshot_source`'s gallery from the top using its
    already-resolved id — preview.py's "Change image" -> "Pick a
    different screenshot" branch, reached only when the current image
    is API-sourced (ticket 9). `cross_provider=True` unconditionally so
    "Wrong anime? Search again" is always offered here, letting the
    starter back out to a different provider entirely if nothing in
    this one's gallery fits.

    Returns the i18n key for the caller's own follow-up message edit —
    `dm_start.screenshot_source_picked` once a gallery is up, otherwise
    a fallback key the caller passes to `reply_with_source_menu`. A
    stale button here (`screenshot_source` cleared by a "Re-search
    title" since this preview message was sent) must degrade
    gracefully, not crash on a bare assert."""
    game.setup_step = SetupStep.PICKING_SCREENSHOT
    provider = game.screenshot_source
    if provider is None:
        logger.warning(
            "Game {}: resume_screenshot_gallery called with no screenshot_source (stale button)",
            game.id,
        )
        # No provider to name or flag — just re-offer the plain menu.
        return "dm_start.pick_screenshot_source_prompt"

    provider_id = _provider_id(game, provider)
    result = await _fetch_screenshots_or_fallback(context, game, provider, provider_id)
    if isinstance(result, Fallback):
        return result

    target = GalleryTarget(
        chat_id=game.starter_id, provider=provider, offset=0, cross_provider=True
    )
    await _show_gallery_page(context, target, result, lang)
    return "dm_start.screenshot_source_picked"


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
        failure = await _resolve_screenshot_source(context, game, provider, lang)
        # Replying inside the session block, while `game` is still live —
        # the source menu is built from it.
        if failure is None:
            await query.edit_message_text(i18n.t("dm_start.screenshot_source_picked", lang))
        else:
            await reply_fallback(query.edit_message_text, game, lang, failure)


async def _resolve_screenshot_source(
    context: ContextTypes.DEFAULT_TYPE, game, provider: str, lang: str
) -> Fallback | None:
    """Runs once a screenshot-source button is tapped: same-provider (an
    id already on file) goes straight to the gallery; cross-provider
    silently searches by the confirmed title first (ticket 8) and only
    falls back if that search finds nothing or the provider is down.
    Once a provider id is in hand either way, a screenshot-fetch failure
    or a genuinely empty result falls back the same way, via
    _fetch_screenshots_or_fallback.

    Returns None once a gallery is on screen, or the i18n key for a
    failure — which the caller hands to `reply_with_source_menu`, so
    every one of these exits leaves the starter on the source menu
    rather than staring at a bare error."""
    provider_id = _provider_id(game, provider)
    cross_provider = provider_id is None
    if cross_provider:
        provider_id = await _resolve_cross_provider_id(context, game, provider)
        if provider_id is None:
            return Fallback("dm_start.cross_provider_search_failed", provider)

    result = await _fetch_screenshots_or_fallback(context, game, provider, provider_id)
    if isinstance(result, Fallback):
        return result

    logger.debug("Game {}: fetched {} {} screenshot(s)", game.id, len(result), provider)
    target = GalleryTarget(
        chat_id=game.starter_id, provider=provider, offset=0, cross_provider=cross_provider
    )
    await _show_gallery_page(context, target, result, lang)
    return None


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
    provider_id = _provider_id(game, provider)
    logger.info("Game {}: cross-provider resolved {} -> id {}", game.id, provider, provider_id)
    return provider_id


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
        # A stale "More screenshots" tap pointing past the end of the
        # list. Not a dead end — the gallery message that button came
        # from is still on screen with all its own buttons — so this
        # stays a plain notice rather than re-showing the source menu.
        logger.warning(
            "Game gallery: {} page at offset {} is past the end of {} url(s)",
            target.provider,
            target.offset,
            len(urls),
        )
        await context.bot.send_message(
            chat_id=target.chat_id,
            text=i18n.t(
                "dm_start.no_screenshots_available",
                lang,
                service=_SERVICE_DISPLAY_NAMES[target.provider],
            ),
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
