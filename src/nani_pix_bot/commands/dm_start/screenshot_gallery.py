"""Browsing an already-shown screenshot gallery — numbered picks, "More
screenshots", downloading the chosen bytes, and the "Wrong anime?
Search again" cross-provider correction sub-flow. Split out of
screenshots.py (which owns source *selection* — the source-selection
keyboard and same-/cross-provider resolution up to the point a gallery
is first shown) once that file's total complexity grew past qlty's
threshold; see MECHANICS.md's "Starting a game" section for the
player-facing flow both files implement together."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start._shared import (
    _IMAGE_DOWNLOAD_ERRORS,
    _SEARCH_SERVICE_ERRORS,
    _client_for_source,
    _reject_stale_tap,
    _show_preview,
)
from nani_pix_bot.commands.dm_start.keyboards import (
    SCREENSHOT_SEARCH_PICK_PREFIX,
    jikan_results_keyboard,
    parse_screenshot_more_callback_data,
    parse_screenshot_pick_callback_data,
    parse_screenshot_search_again_callback_data,
    parse_screenshot_search_pick_callback_data,
    screenshot_source_keyboard,
    shikimori_results_keyboard,
    tmdb_results_keyboard,
)
from nani_pix_bot.commands.dm_start.screenshots import (
    GALLERY_PAGE_SIZE,
    Fallback,
    GalleryTarget,
    SourceMenu,
    _fetch_screenshots_or_fallback,
    _get_provider_by_id,
    _provider_id,
    _show_gallery_page,
    gallery_page_or_fallback,
    reply_fallback,
    reply_with_source_menu,
    source_menu_for,
)
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import SetupStep
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, settings
from nani_pix_bot.services.search import jikan, shikimori, tmdb
from nani_pix_bot.services.search.jikan import JikanResult
from nani_pix_bot.services.search.shikimori import ShikimoriResult
from nani_pix_bot.services.search.tmdb import TMDBResult


@dataclass(frozen=True)
class Tap:
    """How to talk to the starter about the gallery button they just
    tapped: the language to say it in, and the one `answer` the query
    gets.

    Bundled rather than threaded as two parameters because they travel
    together through every branch below and always come from the same
    `CallbackQuery` — and because six parameters is where this file's
    complexity budget (qlty) says a function has stopped having a shape.

    `answer` is the bound `query.answer`, handed down for the same
    reason `query.edit_message_text` is handed to `reply_fallback`: each
    branch reaches the moment the tap is decided at a different point,
    and one of them has something to say when it does (see
    `_handle_screenshot_pick`). Exactly one call per path."""

    lang: str
    answer: Callable[..., Awaitable[bool]]


async def screenshot_search_again_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """ "Wrong anime? Search again" — tapped from a cross-provider
    gallery to correct a bad auto-resolved top result, or from a
    cross-search's own "None of these". Asks for a query;
    screenshot_picker_provider (already set to `provider` by whichever
    step showed this button, re-asserted here so a stale button still
    routes) is what tells search_text_handler to send the next text
    message to _screenshot_search_step below. No image is staged by any
    of this, so screenshot_source stays untouched.

    `setup_step` is re-asserted for the same reason the picker is, and
    it is the other half of the same routing: this button rides on a
    gallery message that stays tappable after the preview has already
    moved the game to CONFIRMING, and search_text_handler's CONFIRMING
    branch drops typed text on the floor. Setting only the picker put
    the starter in front of a prompt whose answer went nowhere — 0
    replies, 0 searches — which no keyboard on that prompt can fix.

    The prompt carries the source-selection keyboard because this reply
    replaces the message whose buttons were the starter's other ways
    forward (a different provider, their own upload) — leaving only a
    typed query would be another buttonless prompt, which is what issue
    #71 asks this button to stop landing on. Not the "When a provider
    fails" rule from MECHANICS.md: nothing has failed on this screen, so
    no provider is flagged either."""
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
        # One answer per query id, so the bare acknowledgement waits
        # until the stale branch above has had its chance at it (see
        # _reject_stale_tap).
        # Inside the open write transaction, like every other await in
        # this block — see issue #82, which is filed against exactly
        # that shape here; this is one more call for its sweep to move,
        # not a new pattern.
        await query.answer()
        provider = parse_screenshot_search_again_callback_data(query.data)
        if provider is None:
            # keyboards.py logged what was wrong with the payload; this
            # names the game whose picker it would otherwise have aimed
            # at a provider that doesn't exist (see #71).
            logger.warning("Game {}: rejected search-again tap {!r}", game.id, query.data)
            return
        game.setup_step = SetupStep.PICKING_SCREENSHOT
        game.screenshot_picker_provider = provider
        # Built while the game is still live — the keyboard below is
        # sent after this block closes.
        providers = source_menu_for(game, None).providers
        logger.debug("Game {}: re-searching {} screenshots by hand", game.id, provider)

    await query.edit_message_text(
        i18n.t("dm_start.ask_search_screenshots", lang),
        reply_markup=screenshot_source_keyboard(providers, lang),
    )


async def _screenshot_search_step(
    message, context: ContextTypes.DEFAULT_TYPE, lang: str, menu: SourceMenu
) -> None:
    """A search query typed while resolving `menu.provider`'s screenshot
    (the "Wrong anime? Search again" correction, or the fallback state
    after an auto-search found nothing) — mirrors search.py's own
    `_search_step`, but wires its results keyboard to
    screenshot_search_pick_callback_handler instead of identification
    search's own pick_callback_handler (see keyboards.py's `pick_prefix`
    override on the shared *_results_keyboard builders).

    Takes the whole `menu` rather than a bare provider because both
    failure exits below re-show the source-selection keyboard: this is
    the step a dead provider used to trap the starter in — type a query,
    get a bare error, repeat, with no button anywhere on screen (a live
    Jikan outage on 2026-09-14 did exactly that). The menu is built by
    the caller, whose session is already closed by the time we run."""
    provider = menu.provider
    assert provider is not None  # search is always for a specific provider
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
        await reply_with_source_menu(
            status_message.edit_text, menu, lang, "dm_start.screenshot_service_down"
        )
        return

    logger.debug(
        "{} screenshot cross-search for {!r} returned {} result(s)",
        provider,
        message.text,
        len(results),
    )
    if not results:
        await reply_with_source_menu(
            status_message.edit_text, menu, lang, "dm_start.screenshot_no_results"
        )
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
        # Built here, where the game is loaded anyway, so the failure
        # paths below can re-show the source menu without a second
        # lookup. The provider is filled in once the pick is parsed.
        menu = source_menu_for(game, None)

    # Deliberately not acknowledged yet, unlike this file's other
    # handlers: the *second* lookup below is another stale-row site, and
    # a query id can only be answered once (see _reject_stale_tap). So
    # the spinner runs for the length of the get_by_id round-trip —
    # which is work actually happening — and every path answers exactly
    # once after it.
    resolved = await _resolve_screenshot_search_pick(query, context, lang, menu)
    if resolved is None:
        # Whatever went wrong already said so on the message itself.
        await query.answer()
        return
    provider, external_id, result = resolved

    with session_scope(session_factory) as session:
        game = game_service.get_setup_game_for_starter(session, user.id)
        if game is None:
            # The second lookup, so this is the narrow window where the
            # row went away *during* the get_by_id round-trip above
            # rather than before the tap. Same message as the first
            # site's, and the line number is the only thing that tells
            # the two apart in a log — these two also share a callback
            # prefix, so `data` doesn't. That line number is the
            # *caller's* solely because _reject_stale_tap logs through
            # `logger.opt(depth=1)`: plain loguru stamps the frame of the
            # logger.warning call itself, which is one line inside the
            # helper and identical for every site. Drop that opt() and
            # this pair goes back to being byte-identical.
            await _reject_stale_tap(query, user.id, lang)
            return
        # Inside the open write transaction, like every other await in
        # this block — see issue #82, which is filed against exactly that
        # shape here; one more call for its sweep to move, not a new
        # pattern.
        await query.answer()
        game_service.set_screenshot_provider_id(game, result)
        logger.debug(
            "Game {}: cross-provider search resolved {} -> id {}", game.id, provider, external_id
        )
        outcome = await _show_search_pick_gallery(context, game, provider, external_id, lang)
        # if/else rather than an early return purely to keep this
        # handler's return count under qlty's threshold.
        if isinstance(outcome, Fallback):
            await reply_fallback(query.edit_message_text, game, lang, outcome)
        else:
            await query.edit_message_text(i18n.t(outcome, lang))


async def _show_search_pick_gallery(
    context: ContextTypes.DEFAULT_TYPE, game, provider: str, external_id: int, lang: str
) -> str | Fallback:
    """Lists the picked title's screenshots and puts its first gallery
    page up — `cross_provider=True` because arriving here *is* the
    cross-provider correction, so the page keeps offering "Wrong anime?
    Search again" for another go. Returns the i18n key for the caller's
    own message edit, or the Fallback that fetching/sending produced."""
    fetched = await _fetch_screenshots_or_fallback(context, game, provider, external_id)
    if isinstance(fetched, Fallback):
        return fetched

    target = GalleryTarget(
        chat_id=game.starter_id, provider=provider, offset=0, cross_provider=True
    )
    return await gallery_page_or_fallback(context, target, fetched, lang)


async def _resolve_screenshot_search_pick(
    query, context: ContextTypes.DEFAULT_TYPE, lang: str, menu: SourceMenu
) -> tuple[str, int, ShikimoriResult | JikanResult | TMDBResult] | None:
    """Parses the pick and re-fetches the full result via get_by_id
    (restart-resilient, same reasoning as search.py's own
    `_resolve_picked_result`). Replies and returns None for every
    already-handled outcome: unparseable callback data, the search
    service erroring, or the id no longer existing — each of the last
    two re-showing `menu` so the starter keeps a way out."""
    parsed = parse_screenshot_search_pick_callback_data(query.data)
    if parsed is None:
        # Forged payload — keyboards.py logged which half didn't hold up.
        # Nothing to reply with: `menu` has no provider yet (the pick is
        # where one would have come from), so there is no failure screen
        # to draw that wouldn't have to invent one.
        logger.warning("Rejected cross-search pick {!r}", query.data)
        return None
    provider, external_id = parsed
    menu = replace(menu, provider=provider)

    client = _client_for_source(context, provider)
    try:
        result = await _get_provider_by_id(provider, client, external_id)
    except _SEARCH_SERVICE_ERRORS:
        logger.exception("{} get_by_id failed for id {}", provider, external_id)
        await reply_with_source_menu(
            query.edit_message_text, menu, lang, "dm_start.screenshot_service_down"
        )
        return None

    if result is None:
        logger.warning("{} id {} picked but no longer found", provider, external_id)
        await reply_with_source_menu(
            query.edit_message_text, menu, lang, "dm_start.not_found_anymore"
        )
        return None

    return provider, external_id, result


async def screenshot_gallery_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """A numbered screenshot or "More screenshots" button tapped from
    the gallery's own keyboard.

    The single answer this query gets is handed *down*: `query.answer`
    goes to `_dispatch_gallery_action` the same way `query.edit_message_text`
    already goes to `reply_fallback`. A query id can only be answered
    once, and one outcome down there wants that answer to carry text (a
    numbered tap whose index is gone) — so answering up here would mean
    either spending it before that is known, or holding it until after
    the album send and the image download, leaving the spinner running
    across the slowest work in the flow for every tap that *works*.
    Each branch answers at its own decision point instead, exactly
    once."""
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
        outcome = await _dispatch_gallery_action(
            context, session, game, query.data, Tap(lang, query.answer)
        )
        # Replied inside the session block — a Fallback's source menu is
        # built from the still-live `game`.
        if isinstance(outcome, Fallback):
            await reply_fallback(query.edit_message_text, game, lang, outcome)
        elif outcome is not None:
            # Already-rendered text, not an i18n key — the paging
            # confirmation needs format kwargs the caller doesn't have.
            await query.edit_message_text(outcome)


async def _dispatch_gallery_action(
    context: ContextTypes.DEFAULT_TYPE, session, game, data: str, tap: Tap
) -> str | Fallback | None:
    """Runs the gallery action `data` encodes (a "More screenshots" page
    or a numbered pick) and returns the rendered text for the
    resulting message, or None for a no-op — split out of
    `screenshot_gallery_callback_handler` itself to keep that handler's
    own return count under qlty's "many returns" threshold.

    Every branch answers `tap` exactly once, at its own decision
    point — see `Tap`."""
    more = parse_screenshot_more_callback_data(data)
    if more is not None:
        return await _handle_more_screenshots(context, game, more, tap)

    picked = parse_screenshot_pick_callback_data(data)
    if picked is None:
        # app.py only routes screenshot_pick:/screenshot_more: here, so
        # neither parser recognising `data` means it was forged — the
        # payload after the prefix is whatever the client chose to send.
        # keyboards.py logged which half didn't hold up; this says which
        # game the forged tap was aimed at.
        logger.warning("Game {}: rejected gallery tap {!r}", game.id, data)
        # No feedback by design — a real client cannot produce this
        # payload — but the spinner still has to stop.
        await tap.answer()
        return None
    return await _handle_screenshot_pick(context, session, game, picked, tap)


async def _handle_more_screenshots(
    context: ContextTypes.DEFAULT_TYPE, game, more: tuple[str, int], tap: Tap
) -> str | Fallback | None:
    """Renders the gallery page at the tapped offset. Serves the back
    button as well as the forward one — the callback has always encoded
    an absolute offset rather than a direction — so the confirmation
    names the range shown instead of saying "here are some more", which
    would be wrong half the time.

    `cross_provider=True` unconditionally, like `resume_screenshot_gallery`
    and for the same reason: the `screenshot_more:<provider>:<offset>`
    payload has nowhere to carry the flag (Telegram caps callback_data at
    64 bytes, so an extra field is expensive), and rendering "Wrong
    anime? Search again" on every page is the right side to err on —
    paging is precisely what a starter does when the auto-resolved title
    looks wrong, so dropping the escape hatch on page 2 took it away at
    the exact moment it was wanted.

    Drawing that button means arming the picker, exactly as
    `resume_screenshot_gallery` does for the same reason: tapping it
    re-arms the column itself, but a *typed* correction has only the
    column to route on, and search.py drops text with no provider being
    resolved. Page 2 of a same-provider gallery therefore used to show
    "Wrong anime? Search again" over a prompt that silently swallowed
    anything typed under it. The write always names the provider whose
    gallery is being drawn, so it cannot point the router at some other
    provider's search."""
    provider, offset = more
    # Answered before the work, not after: nothing this branch can
    # discover changes what the tap is answered *with*, and the page
    # below is an album Telegram fetches from five remote URLs — a
    # spinner running that long on the flow's most-tapped button is
    # latency the starter can see.
    await tap.answer()
    provider_id = _provider_id(game, provider)
    result = await _fetch_screenshots_or_fallback(context, game, provider, provider_id)
    if isinstance(result, Fallback):
        return result

    shown = len(result[offset : offset + GALLERY_PAGE_SIZE])
    if not shown:
        # A stale forward tap against a list that shrank since this page
        # was drawn. Handing it to _show_gallery_page sent a bare "no
        # screenshots" notice *and* let the caller edit this message's
        # own keyboard away in favour of a nonsense range ("Screenshots
        # 11-10 of 2"), leaving two messages with no buttons on either —
        # the notice even names buttons ("pick another source below")
        # that weren't there. It is an ordinary failure, so it takes the
        # ordinary failure exit: the same notice, with the source menu
        # under it and the picker re-armed (see reply_fallback).
        logger.warning(
            "Game {}: stale {} page at offset {} is past the end of {} url(s)",
            game.id,
            provider,
            offset,
            len(result),
        )
        return Fallback("dm_start.no_screenshots_available", provider)

    game.screenshot_picker_provider = provider
    target = GalleryTarget(
        chat_id=game.starter_id, provider=provider, offset=offset, cross_provider=True
    )
    fallback = await _show_gallery_page(context, target, result, tap.lang)
    if fallback is not None:
        return fallback

    logger.debug(
        "Game {}: showing {} gallery page at offset {} ({} url(s) total)",
        game.id,
        provider,
        offset,
        len(result),
    )
    return i18n.t(
        "dm_start.gallery_page_sent",
        tap.lang,
        first=offset + 1,
        last=offset + shown,
        total=len(result),
    )


async def _handle_screenshot_pick(
    context: ContextTypes.DEFAULT_TYPE, session, game, picked: tuple[str, int], tap: Tap
) -> str | Fallback | None:
    """Downloads the picked screenshot's bytes and shows the
    confirmation preview. Returns the i18n key for the caller's own
    follow-up message edit, or None for a stale-button no-op (the
    tapped index is out of range against a still-successful fetch —
    distinct from a fetch failure/empty result, which
    _fetch_screenshots_or_fallback already turns into its own fallback
    key and setup_step transition).

    This is the branch `Tap.answer` is threaded down for: the
    stale-index verdict is known right after the (normally cached) url
    listing and before the download, so the tap gets answered — with
    text, in that one case — without the starter waiting out a download
    and a five-stage preview album first."""
    provider, index = picked
    provider_id = _provider_id(game, provider)
    result = await _fetch_screenshots_or_fallback(context, game, provider, provider_id)
    if isinstance(result, Fallback):
        await tap.answer()
        return result
    urls = result
    if index >= len(urls):
        # Deliberately no message *edit*: unlike the paging equivalent
        # above, the gallery this was tapped from is left exactly as it
        # was, keyboard and all, so the starter still has every other
        # screenshot to pick and nothing to be rescued from. What they
        # were missing is any sign the tap registered at all — hence the
        # plain toast (no alert: nothing here needs dismissing) on top of
        # the log line a rejected action gets. That toast *is* this
        # path's answer, which is the whole reason the query's answer is
        # threaded down here rather than spent by the handler.
        logger.warning(
            "Game {}: stale {} pick #{} against {} url(s)",
            game.id,
            provider,
            index + 1,
            len(urls),
        )
        await tap.answer(i18n.t("dm_start.screenshot_gone", tap.lang))
        return None

    # These are external URLs (Shikimori/Jikan/TMDB), not Telegram
    # file_ids — the gallery album itself lets Telegram fetch them
    # server-side, but storing one as original_image needs the actual
    # bytes downloaded ourselves. Routed through _client_for_source
    # (not the bare search_client) so a TMDB pick downloads through the
    # same proxied, Bearer-authed client its search/screenshots calls
    # already use — TMDB's image CDN may be behind the same DNS block
    # as api.themoviedb.org (see ARCHITECTURE.md's connectivity notes).
    # _IMAGE_DOWNLOAD_ERRORS, not _SEARCH_SERVICE_ERRORS: this is a plain
    # GET against a CDN, so only a transport error means "the provider is
    # unreachable" — see _shared.py for why the broader tuple is the
    # wrong one to reuse here.
    # Decided: a real pick, and the download is about to start — so the
    # spinner stops before it rather than after (that download plus the
    # preview album is the longest stretch in the flow).
    await tap.answer()
    download_client = _client_for_source(context, provider)
    try:
        response = await download_client.get(urls[index])
        response.raise_for_status()
    except _IMAGE_DOWNLOAD_ERRORS:
        logger.exception(
            "Game {}: downloading {} screenshot #{} failed", game.id, provider, index + 1
        )
        return Fallback("dm_start.screenshot_service_down", provider)

    # The one place image provenance is written: these bytes really do
    # come from `provider`'s *_id on file. The picker is done resolving
    # at the same moment — the next screen is the confirmation preview.
    game.original_image = response.content
    game.screenshot_source = provider
    game.screenshot_picker_provider = None
    logger.debug("Game {}: picked {} screenshot #{}", game.id, provider, index + 1)

    await _show_preview(context, session, game, tap.lang)
    return i18n.t("dm_start.preview_sent", tap.lang)
