"""Method selection dispatch + the AniList/Shikimori search-and-pick
flow. Manual entry (a different `source`) and the preview's follow-up
steps (`CONFIRMING`/`AWAITING_SYNONYM`/`AWAITING_PHOTO_CHANGE`) are
dispatched from here too, since they all arrive as the same kind of DM
text message — see `search_text_handler`."""

from typing import Literal

import httpx
from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start._shared import (
    _SEARCH_SERVICE_ERRORS,
    _client_for_source,
    _post_preview_album,
    _reject_stale_tap,
    _reply_service_down,
    _search_and_build_keyboard,
    _stage_preview,
    _stored_provider,
)
from nani_pix_bot.commands.dm_start.keyboards import (
    SEARCH_RETRY_CALLBACK_DATA,
    anilist_results_keyboard,
    jikan_results_keyboard,
    parse_method_callback_data,
    parse_pick_callback_data,
    shikimori_results_keyboard,
    tmdb_results_keyboard,
)
from nani_pix_bot.commands.dm_start.manual import _manual_synonyms_step, _manual_title_step
from nani_pix_bot.commands.dm_start.preview import _add_synonym_step
from nani_pix_bot.commands.dm_start.screenshot_gallery import _screenshot_search_step
from nani_pix_bot.commands.dm_start.screenshots import (
    send_screenshot_picker_prompt,
    source_menu_for,
    stage_screenshot_picker,
)
from nani_pix_bot.commands.helpers.scoping import is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import Provider, SetupStep
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, settings
from nani_pix_bot.services.search import anilist, jikan, shikimori, tmdb
from nani_pix_bot.services.search.anilist import AniListResult
from nani_pix_bot.services.search.jikan import JikanResult
from nani_pix_bot.services.search.shikimori import ShikimoriResult
from nani_pix_bot.services.search.tmdb import TMDBResult


def _search_prompt_key(*, source: Provider | Literal["manual"], has_image: bool) -> str:
    """Which "now tell me what it is" prompt to show after a method pick.
    The photo-first entry point already has the screenshot in hand, so it
    can say "what anime is *this* from?"; `/newgame` has nothing to point
    at yet and needs its own wording. Manual entry's prompt is neutral
    about whether an image exists, so both entry points share it."""
    if source == "manual":
        return "dm_start.ask_manual_title"
    return "dm_start.ask_search" if has_image else "dm_start.ask_search_newgame"


async def method_pick_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The starter tapped AniList or Shikimori from photo_handler's
    keyboard. Stores the choice on the still-SETUP game row (rather than
    user_data) so search_text_handler knows which service to use even
    after a restart."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()

    source = parse_method_callback_data(query.data)
    user = query.from_user
    if source is None or user is None:
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        setup_game = game_service.get_setup_game_for_starter(session, user.id)
        if setup_game is None:
            return
        setup_game.source = source
        setup_game.setup_step = SetupStep.PICKING_METHOD
        # Read inside the session block — the prompt below depends on it,
        # and the row is detached once the block closes.
        has_image = setup_game.original_image is not None
        logger.debug("Game {}: starter picked identification method {!r}", setup_game.id, source)

    await query.edit_message_text(
        i18n.t(_search_prompt_key(source=source, has_image=has_image), lang)
    )


async def search_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A DM text message, once a game is pending, is either a manual
    title/synonym entry or a search query for whichever service
    (AniList/Shikimori) the starter picked."""
    message = update.message
    user = update.effective_user
    if not is_private_chat(update) or message is None or message.text is None or user is None:
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        setup_game = game_service.get_setup_game_for_starter(session, user.id)
        if setup_game is None:
            return
        setup_step = setup_game.setup_step
        source = setup_game.source
        awaiting_synonyms = setup_game.title_english is not None
        # The picker column, never the image one: "which provider is
        # being resolved" is exactly the question routing a typed
        # message asks, and screenshot_source answers a different one
        # ("what backs the stored image") — see models/game.py.
        picker_provider = setup_game.screenshot_picker_provider
        # Captured while the game is live: _screenshot_search_step runs
        # after this block closes and needs the source menu to fall back
        # onto if the provider is down or finds nothing.
        screenshot_menu = (
            source_menu_for(setup_game, _stored_provider(picker_provider))
            if picker_provider is not None
            else None
        )

    if setup_step == SetupStep.AWAITING_SYNONYM:
        await _add_synonym_step(message, context, lang, user)
    elif setup_step in (SetupStep.CONFIRMING, SetupStep.AWAITING_PHOTO_CHANGE):
        pass  # only the preview's buttons (or a replacement photo) matter here
    elif setup_step == SetupStep.PICKING_SCREENSHOT:
        if screenshot_menu is not None:
            # A screenshot provider is being resolved — ticket 8's
            # cross-provider "Search again" correction, the fallback
            # state after an auto-search found nothing or a provider
            # failed, or (ticket 9) the preview's "Pick a different
            # screenshot" re-opening a gallery that still has an *old*
            # image staged until a new one is actually picked, so
            # `original_image` being set here does NOT mean no query is
            # expected (issue: a typed correction used to be silently
            # swallowed in exactly that case). Anything else during
            # this step — the source-selection keyboard, or a
            # same-provider gallery, neither of which offers a search —
            # expects a button tap, not text.
            await _screenshot_search_step(message, context, lang, screenshot_menu)
        else:
            logger.debug(
                "Starter {}: ignoring text during PICKING_SCREENSHOT — no provider is being "
                "resolved, so this screen expects a button tap",
                user.id,
            )
    elif source == "manual":
        if awaiting_synonyms:
            await _manual_synonyms_step(message, context, lang, user)
        else:
            await _manual_title_step(message, context, lang, user)
    else:
        # Everything that isn't "manual" is one of the four providers, so
        # this is where the column's bare string becomes a real member
        # (see `_stored_provider`) — `_search_step` names it on two
        # screens via `.display_name`.
        await _search_step(message, context, lang, _stored_provider(source))


async def _search_step(
    message, context: ContextTypes.DEFAULT_TYPE, lang: str, source: Provider
) -> None:
    """An AniList/Shikimori search query: search and show a results
    keyboard, or fail back to the method-selection keyboard. Sends a
    "searching" message immediately — the round-trip can take a few
    seconds — and edits that same message in place with the eventual
    outcome, so the starter gets fast feedback without extra message
    clutter."""
    status_message = await message.reply_text(i18n.t("dm_start.searching", lang))
    logger.debug("{} search started for query {!r}", source, message.text)

    client = _client_for_source(context, source)
    try:
        if source == Provider.SHIKIMORI:
            results, keyboard = await _search_and_build_keyboard(
                client,
                message.text,
                shikimori.search,
                lambda rs: shikimori_results_keyboard(rs, lang),
            )
        elif source == Provider.JIKAN:
            results, keyboard = await _search_and_build_keyboard(
                client, message.text, jikan.search, lambda rs: jikan_results_keyboard(rs, lang)
            )
        elif source == Provider.TMDB:
            results, keyboard = await _search_and_build_keyboard(
                client, message.text, tmdb.search, lambda rs: tmdb_results_keyboard(rs, lang)
            )
        else:
            results, keyboard = await _search_and_build_keyboard(
                client, message.text, anilist.search, lambda rs: anilist_results_keyboard(rs, lang)
            )
    except _SEARCH_SERVICE_ERRORS:
        logger.exception("{} search failed for query {!r}", source, message.text)
        await _reply_service_down(status_message.edit_text, lang, source)
        return

    logger.debug("{} search for {!r} returned {} results", source, message.text, len(results))
    if not results:
        await status_message.edit_text(
            i18n.t("dm_start.no_results", lang, service=source.display_name)
        )
        return

    await status_message.edit_text(i18n.t("dm_start.pick_prompt", lang), reply_markup=keyboard)


async def pick_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The starter tapped a result (or "none of these") from
    search_text_handler's keyboard. A valid pick pixelates the original
    at the first (blockiest) stage, posts it to the group's game topic,
    and activates the game."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    user = query.from_user
    if user is None:
        # Never acknowledged on this branch, unlike before the reorder
        # below: Telegram marks `from_user` required on a CallbackQuery,
        # so this is unreachable in practice — same shape as the
        # equivalent guard in screenshot_gallery.py's handlers, none of
        # which answer here either.
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)

    # Deliberately not acknowledged yet: _resolve_picked_result runs a
    # provider round-trip, and the SETUP row can vanish while it's in
    # flight — the lookup below is a stale-row site (issue #95, the one
    # remaining silent one from #79's sweep), and a query id can only be
    # answered once (see _reject_stale_tap). Every already-handled
    # outcome inside _resolve_picked_result replies via
    # `query.edit_message_text` instead of `query.answer`, so the bare
    # answer just below still lands as this handler's one acknowledgement
    # for those paths.
    resolved = await _resolve_picked_result(query, context, lang)
    if resolved is None:
        await query.answer()
        return
    source, external_id, result = resolved

    with session_scope(session_factory) as session:
        setup_game = game_service.get_setup_game_for_starter(session, user.id)
        if setup_game is None:
            await _reject_stale_tap(query, user.id, lang)
            return
        # Inside the open write transaction, like every other await in
        # this block — see issue #82, which is filed against exactly
        # that shape here; this is one more call for its sweep to move,
        # not a new pattern.
        await query.answer()
        game_service.stage_result(setup_game, result, source=source)
        logger.debug("Game {}: staged {} result {}", setup_game.id, source, external_id)
        has_image = setup_game.original_image is not None
        if has_image:
            # Traditional photo-first entry — image already in hand.
            album = _stage_preview(session, setup_game, lang)
            message_key = "dm_start.preview_sent"
        else:
            # Screenshot-less /newgame entry — pick a screenshot next.
            picker_prompt = stage_screenshot_picker(setup_game)
            message_key = "dm_start.identification_staged"
    # Block closed and committed above — see _post_preview_album's/
    # send_screenshot_picker_prompt's docstrings for why the send has to
    # happen after.
    if has_image:
        await _post_preview_album(context, album, lang)
    else:
        await send_screenshot_picker_prompt(context, picker_prompt, lang)

    await query.edit_message_text(i18n.t(message_key, lang))


async def _get_identification_result(
    provider: Provider, client: httpx.AsyncClient, external_id: int
) -> AniListResult | ShikimoriResult | JikanResult | TMDBResult | None:
    return await provider.search_module.get_by_id(client, external_id)


async def _resolve_picked_result(
    query, context: ContextTypes.DEFAULT_TYPE, lang: str
) -> tuple[Provider, int, AniListResult | ShikimoriResult | JikanResult | TMDBResult] | None:
    """Handles the "none of these" retry tap and resolves a valid pick to
    its (source, external_id, result) triple. Replies and returns None
    for every already-handled outcome: retry tapped, unparseable
    callback data, the search service erroring, or the id no longer
    existing."""
    if query.data == SEARCH_RETRY_CALLBACK_DATA:
        await query.edit_message_text(i18n.t("dm_start.retry", lang))
        return None

    parsed = parse_pick_callback_data(query.data)
    if parsed is None:
        return None
    source, external_id = parsed

    client = _client_for_source(context, source)
    try:
        result = await _get_identification_result(source, client, external_id)
    except _SEARCH_SERVICE_ERRORS:
        logger.exception("{} get_by_id failed for id {}", source, external_id)
        await _reply_service_down(query.edit_message_text, lang, source)
        return None

    if result is None:
        logger.warning("{} id {} picked but no longer found", source, external_id)
        await query.edit_message_text(
            i18n.t("dm_start.not_found_anymore", lang, service=source.display_name)
        )
        return None

    return source, external_id, result
