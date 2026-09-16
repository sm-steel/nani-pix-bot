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
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start._shared import (
    _SEARCH_SERVICE_ERRORS,
    _client_for_source,
    _reject_stale_tap,
    _screenshot_capable_providers,
    _stored_provider,
)
from nani_pix_bot.commands.dm_start.keyboards import (
    GalleryPage,
    parse_screenshot_source_callback_data,
    screenshot_gallery_keyboard,
    screenshot_source_keyboard,
)
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import Provider, SetupStep
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, settings
from nani_pix_bot.services.search.jikan import JikanResult
from nani_pix_bot.services.search.shikimori import ShikimoriResult
from nani_pix_bot.services.search.tmdb import TMDBResult

GALLERY_PAGE_SIZE = 5

# `resume_screenshot_gallery`'s one no-provider outcome: a stale "pick a
# different screenshot" tap on a game whose screenshot_source was
# cleared meanwhile. Named (rather than returned as a bare string) so
# preview.py can tell it apart from `dm_start.screenshot_source_picked`
# — that one lands on a gallery message carrying its own buttons, this
# one has to be rendered *with* the source menu or the starter is left
# with nothing to tap. Placeholder-free, as SourceMenu.provider=None
# requires.
NO_SOURCE_PROMPT_KEY = "dm_start.pick_screenshot_source_prompt"


@dataclass(frozen=True)
class SourceMenu:
    """The source-selection menu, plus which provider is currently in
    play. Bundled into one object so the failure helpers below stay
    under qlty's parameter threshold (same reasoning as GalleryTarget
    above), and so `_screenshot_search_step` — which runs after its
    caller's session has closed and so can't look the game up itself —
    can be handed everything it needs to draw the menu in one value."""

    providers: list[Provider]
    # None only for the one failure with no provider to blame: a stale
    # "pick a different screenshot" button on a game whose
    # screenshot_source was cleared meanwhile. Nothing gets flagged and
    # the message carries no {service}.
    provider: Provider | None


@dataclass(frozen=True)
class ScreenshotFailure:
    """A screenshot-sub-flow failure, propagated up to whichever handler
    owns the reply: the message to show and the provider to blame. Every
    one of these ends at `reply_with_source_menu` — carrying the provider
    along means no caller has to remember which one it was asking about."""

    key: str
    provider: Provider


def _provider_id(game: Game, provider: Provider) -> int | None:
    """Typed wrapper around the getattr(game, provider.id_attr_name)
    dance used throughout this module — a bare getattr on a dynamic
    attribute name is `Any` to `ty`, which silently let a stale/cleared
    id (e.g. after clear_screenshot_selection) flow into a screenshot
    fetch as if it were always a real int. Routing every read through
    here means a caller has to explicitly deal with the `None` case."""
    return getattr(game, provider.id_attr_name)


async def _fetch_screenshots(provider: Provider, client, provider_id: int) -> list[str]:
    return await provider.screenshot_module.screenshots(client, provider_id)


async def _fetch_screenshots_or_fallback(
    context: ContextTypes.DEFAULT_TYPE, game, provider: Provider, provider_id: int | None
) -> list[str] | ScreenshotFailure:
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
        return ScreenshotFailure("dm_start.no_screenshots_available", provider)

    client = _client_for_source(context, provider)
    try:
        urls = await _fetch_screenshots(provider, client, provider_id)
    except _SEARCH_SERVICE_ERRORS:
        logger.exception(
            "Game {}: fetching {} screenshots failed for id {}", game.id, provider, provider_id
        )
        return ScreenshotFailure("dm_start.screenshot_service_down", provider)

    if not urls:
        logger.info("Game {}: {} has no screenshots for id {}", game.id, provider, provider_id)
        return ScreenshotFailure("dm_start.no_screenshots_available", provider)

    return urls


async def _search_provider(
    provider: Provider, client, query: str
) -> list[ShikimoriResult] | list[JikanResult] | list[TMDBResult]:
    return await provider.screenshot_module.search(client, query)


async def _get_provider_by_id(
    provider: Provider, client, external_id: int
) -> ShikimoriResult | JikanResult | TMDBResult | None:
    return await provider.screenshot_module.get_by_id(client, external_id)


@dataclass(frozen=True)
class GalleryTarget:
    """Where a gallery page is being sent and which provider/offset
    it's paginating — bundled into one object so `_show_gallery_page`
    doesn't need a 6-argument signature (a qlty "many parameters"
    smell). `cross_provider` defaults to False since every caller in
    this ticket is same-provider; ticket 8 will pass True from its own
    cross-provider-resolution path."""

    chat_id: int
    provider: Provider
    offset: int
    cross_provider: bool = False


def source_menu_for(game: Game, provider: Provider | None) -> SourceMenu:
    return SourceMenu(providers=_screenshot_capable_providers(game), provider=provider)


async def reply_fallback(send, game: Game, lang: str, fallback: ScreenshotFailure) -> None:
    """`reply_with_source_menu` for the common case where the caller has
    the game loaded and so can build the menu itself.

    Puts the game back on PICKING_SCREENSHOT *and* points the picker at
    the provider being blamed, since that is literally the screen being
    shown — whatever step the failure interrupted (a gallery pick, a
    "More screenshots" page, the preview's "pick a different
    screenshot"), the starter is now looking at the source-selection
    menu again and a typed query has to route to the cross-provider
    search.

    The picker half matters most for a failure reached *from a
    same-provider gallery*: showing that gallery clears the picker
    (nothing is being resolved while it is up), so without re-arming it
    here a retyped query would be silently dropped by
    search_text_handler — the exact escape MECHANICS.md's "When a
    provider fails" promises. Setting it at this one chokepoint covers
    every provider-flagged failure screen uniformly, and can't
    resurrect the same-provider-gallery bug: this only ever runs when
    the source menu is *replacing* a gallery, never while one is up."""
    game.setup_step = SetupStep.PICKING_SCREENSHOT
    game.screenshot_picker_provider = fallback.provider
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
    service = {"service": menu.provider.display_name} if menu.provider else {}
    await send(
        i18n.t(key, lang, **service),
        reply_markup=screenshot_source_keyboard(
            menu.providers, lang, failed_provider=menu.provider
        ),
    )


async def gallery_page_or_fallback(
    context: ContextTypes.DEFAULT_TYPE, target: GalleryTarget, urls: list[str], lang: str
) -> str | ScreenshotFailure:
    """`_show_gallery_page` for the callers whose own return value is
    "the i18n key for the message to edit the tapped message down to":
    the gallery's own confirmation once a page is up, or the page's
    ScreenshotFailure if Telegram wouldn't take it. Saves each of them repeating
    the same three-line None check around a call whose success value is
    always the same constant."""
    fallback = await _show_gallery_page(context, target, urls, lang)
    return fallback if fallback is not None else "dm_start.screenshot_source_picked"


@dataclass(frozen=True)
class ScreenshotPickerPrompt:
    """What send_screenshot_picker_prompt needs, captured while the
    staging game's session was still open — see stage_screenshot_picker's
    docstring for why the send happens after that session closes."""

    starter_id: int
    providers: list[Provider]


def stage_screenshot_picker(game: Game) -> ScreenshotPickerPrompt:
    """DB-phase half of the screenshot-source picker: moves the game to
    PICKING_SCREENSHOT. The caller commits this in its own session_scope
    block, then calls send_screenshot_picker_prompt with the result once
    that block has closed — same "commit before send" reasoning as
    _shared.py's _stage_preview/_post_preview_album split, and for the
    same reason (see jobs/timers/current_image.py's post_current_image
    docstring).

    Called from `search.py`'s `pick_callback_handler` (and `manual.py`'s
    synonym step) once identification is staged with no image yet. Every
    screenshot-capable provider is always offered (see
    _screenshot_capable_providers) since cross-provider resolution means
    even an AniList/manual identification can still get a
    Shikimori/Jikan/TMDB screenshot."""
    providers = _screenshot_capable_providers(game)
    game.setup_step = SetupStep.PICKING_SCREENSHOT
    return ScreenshotPickerPrompt(starter_id=game.starter_id, providers=providers)


async def send_screenshot_picker_prompt(
    context: ContextTypes.DEFAULT_TYPE, prompt: ScreenshotPickerPrompt, lang: str
) -> None:
    await context.bot.send_message(
        chat_id=prompt.starter_id,
        text=i18n.t("dm_start.pick_screenshot_source_prompt", lang),
        reply_markup=screenshot_source_keyboard(prompt.providers, lang),
    )


def clear_screenshot_selection(game: Game) -> None:
    """Leaves the screenshot sub-flow behind: the picker stops resolving
    anything, and an API-picked screenshot plus the provider id that
    resolved it are dropped. Used by preview.py's "Re-search title"
    (a re-search that picks a different anime shouldn't leave the old
    anime's screenshot attached) and by intake.py, where a genuine
    upload supersedes whatever was staged before.

    Only an *image* clears the id: `screenshot_source` is None whenever
    nothing API-sourced backs `original_image`, which covers both a
    genuine upload and a picker that was merely pointed at a provider
    without ever picking from it. That second case is why this reads
    `screenshot_source` and not the picker column — the id it would
    otherwise null out is the identification id, deliberately kept so a
    later cross-search can reuse it (MECHANICS.md's "Starting a game")."""
    game.screenshot_picker_provider = None
    if game.screenshot_source is None:
        return
    # The column hands back a bare str, not a `Provider` (see
    # `_stored_provider`), so `.id_attr_name` needs the conversion first
    # — a plain str has no such attribute.
    setattr(game, _stored_provider(game.screenshot_source).id_attr_name, None)
    game.original_image = None
    game.screenshot_source = None


async def resume_screenshot_gallery(
    context: ContextTypes.DEFAULT_TYPE, game, lang: str
) -> str | ScreenshotFailure:
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
    gracefully, not crash on a bare assert — it returns
    NO_SOURCE_PROMPT_KEY, which the caller must render *with* the source
    menu (there is no gallery message to carry buttons in that case)."""
    game.setup_step = SetupStep.PICKING_SCREENSHOT
    stored = game.screenshot_source
    if stored is None:
        logger.warning(
            "Game {}: resume_screenshot_gallery called with no screenshot_source (stale button)",
            game.id,
        )
        # No provider to name or flag — just re-offer the plain menu.
        return NO_SOURCE_PROMPT_KEY

    # The column hands back a bare str, so this is the one place in this
    # module that has to say so — see `_stored_provider`.
    provider = _stored_provider(stored)

    # The gallery below is drawn cross_provider=True, so it carries
    # "Wrong anime? Search again" — which means a typed correction has
    # to route to _screenshot_search_step, which is what the picker
    # column is for.
    game.screenshot_picker_provider = provider
    provider_id = _provider_id(game, provider)
    result = await _fetch_screenshots_or_fallback(context, game, provider, provider_id)
    if isinstance(result, ScreenshotFailure):
        return result

    target = GalleryTarget(
        chat_id=game.starter_id, provider=provider, offset=0, cross_provider=True
    )
    return await gallery_page_or_fallback(context, target, result, lang)


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
    user = query.from_user
    if user is None:
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        game = game_service.get_setup_game_for_starter(session, user.id)
        if game is None:
            await _reject_stale_tap(query, user.id, lang)
            return
        # Acknowledged here rather than above the lookup: a query id can
        # only be answered once, and the stale branch needs that answer
        # to carry its alert (see _reject_stale_tap).
        await query.answer()
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
    user = query.from_user
    if user is None:
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        game = game_service.get_setup_game_for_starter(session, user.id)
        if game is None:
            await _reject_stale_tap(query, user.id, lang)
            return
        # One answer per query id, so this waits until the stale branch
        # above has had its chance at it (see _reject_stale_tap). Still
        # ahead of the provider round-trip below, so the spinner clears
        # at the same moment it always did for a tap that works.
        # Inside the open write transaction, like every other await in
        # this block — see issue #82, which is filed against exactly
        # that shape here; this is one more call for its sweep to move,
        # not a new pattern.
        await query.answer()

        provider = parse_screenshot_source_callback_data(query.data)
        if provider is None:
            # keyboards.py already logged what was wrong with the payload
            # itself; this says which screen it was aimed at and which
            # game it would have moved, neither of which it can see.
            logger.warning("Game {}: rejected screenshot-source tap {!r}", game.id, query.data)
            return
        # The picker column is written by whichever screen this ends on,
        # not here: _resolve_screenshot_source sets it for the gallery it
        # shows, and reply_fallback sets it for a failure screen. Note
        # it is never screenshot_source — no image exists yet, and this
        # tap must not claim one (see models/game.py).
        failure = await _resolve_screenshot_source(context, game, provider, lang)
        # Replying inside the session block, while `game` is still live —
        # the source menu is built from it.
        if failure is None:
            await query.edit_message_text(i18n.t("dm_start.screenshot_source_picked", lang))
        else:
            await reply_fallback(query.edit_message_text, game, lang, failure)


async def _resolve_screenshot_source(
    context: ContextTypes.DEFAULT_TYPE, game, provider: Provider, lang: str
) -> ScreenshotFailure | None:
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
            return ScreenshotFailure("dm_start.cross_provider_search_failed", provider)

    result = await _fetch_screenshots_or_fallback(context, game, provider, provider_id)
    if isinstance(result, ScreenshotFailure):
        return result

    logger.debug("Game {}: fetched {} {} screenshot(s)", game.id, len(result), provider)
    # A cross-provider gallery carries "Wrong anime? Search again", so a
    # typed correction still has to reach this provider's search. A
    # same-provider one has nothing left to resolve — the id came from
    # identification, and it offers no such button — so a typed message
    # there is not a correction query and must not be searched as one.
    game.screenshot_picker_provider = provider if cross_provider else None
    target = GalleryTarget(
        chat_id=game.starter_id, provider=provider, offset=0, cross_provider=cross_provider
    )
    return await _show_gallery_page(context, target, result, lang)


async def _resolve_cross_provider_id(
    context: ContextTypes.DEFAULT_TYPE, game, provider: Provider
) -> int | None:
    """Silently searches `provider` by the already-confirmed title and
    stages its top result's id onto the game
    (game_service.set_screenshot_provider_id) — ticket 8's cross-
    provider resolution. Returns None on zero results or a search
    failure so the caller can fall back to asking for a manual query
    (the "Wrong anime? Search again" flow lands in that exact same
    fallback state, see search_text_handler's PICKING_SCREENSHOT
    branch in search.py)."""
    # Every stored variant, not just the Latin-script two: AniList often
    # has no English title and TMDB has no romaji one at all, so a game
    # can easily reach here identified by its native (or, from
    # Shikimori, its Russian) title alone — which used to be searched
    # for as `""`. That degraded into the manual-query fallback rather
    # than breaking, but it asked the starter to type a title the game
    # already knows.
    #
    # Same order as prioritized_title()'s non-RU one (services/game/
    # state.py) — a fourth restatement of it, tied to that function by
    # intent but not by code, so a reorder there wants a look here.
    # Deliberately not display_title() itself, which is why this is a
    # copy: a *search query* must not follow the bot's display language
    # (a RU bot would then send Jikan/TMDB a Russian title even when an
    # English one is on file), and display_title's "?" fallback for a
    # title-less game would be a query rather than the no-query this
    # still wants. Native before Russian for the same reason — all three
    # providers index the Japanese title, only Shikimori knows the
    # Russian one.
    #
    # next() over the tuple rather than an `or` chain: five chained
    # operands are a qlty "complex binary expression", and the two read
    # the same anyway (first truthy, else the default).
    query_text = next(
        (
            title
            for title in (
                game.title_english,
                game.title_romaji,
                game.title_native,
                game.title_russian,
            )
            if title
        ),
        "",
    )
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
) -> ScreenshotFailure | None:
    """Sends an album of up to GALLERY_PAGE_SIZE numbered screenshots
    starting at `target.offset`, followed by the gallery's own buttons
    message. Telegram fetches media-group photos server-side from a URL
    directly — no need to download bytes ourselves until one is
    actually picked.

    Handing those URLs to Telegram is also what makes this the one place
    a *Telegram* error means "this provider is unreachable": if it can't
    fetch one (hotlink blocking, still over 10MB, a dead CDN path) it
    answers with a BadRequest, which used to escape every caller and
    reach app._error_handler with no reply going out at all. Returns a
    ScreenshotFailure instead, so it takes the same source-menu exit as the
    provider timing out — every caller already had to handle one."""
    shown = urls[target.offset : target.offset + GALLERY_PAGE_SIZE]
    if not shown:
        # Defensive only: every caller either passes offset 0 against a
        # list _fetch_screenshots_or_fallback already guaranteed is
        # non-empty, or (paging) checks the slice itself and falls back
        # to the source menu. It used to be the paging path's actual
        # landing spot, on the since-disproved reasoning that "the
        # gallery message that button came from is still on screen with
        # all its own buttons" — it is not: the caller edits that very
        # message, so this notice went out bare *and* took the only
        # keyboard with it. A bare notice is safe only because nothing
        # reaches it; anything that starts to must fall back instead.
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
                service=target.provider.display_name,
            ),
        )
        return None

    media = [
        InputMediaPhoto(media=url, caption=str(target.offset + i + 1))
        for i, url in enumerate(shown)
    ]

    has_more = len(urls) > target.offset + len(shown)
    page = GalleryPage(
        provider=target.provider,
        offset=target.offset,
        count=len(shown),
        has_more=has_more,
        cross_provider=target.cross_provider,
        # None on the first page — nothing to go back to. Clamped rather
        # than allowed negative, so a gallery reached at an odd offset
        # (a stale button) still steps back to a valid page.
        previous_offset=max(target.offset - GALLERY_PAGE_SIZE, 0) if target.offset else None,
    )
    # Both sends, not just the album: an album that lands without its
    # buttons message is a gallery nobody can pick from, which is the
    # same dead end by a different route.
    try:
        await context.bot.send_media_group(chat_id=target.chat_id, media=media)
        await context.bot.send_message(
            chat_id=target.chat_id,
            text=i18n.t("dm_start.pick_screenshot_prompt", lang),
            reply_markup=screenshot_gallery_keyboard(page, lang),
        )
    except TelegramError:
        logger.exception(
            "Telegram refused the {} gallery page at offset {} ({} url(s))",
            target.provider,
            target.offset,
            len(urls),
        )
        return ScreenshotFailure("dm_start.screenshot_service_down", target.provider)

    return None
