"""Method selection dispatch + the AniList/Shikimori search-and-pick
flow. Manual entry (a different `source`) and the preview's follow-up
steps (`CONFIRMING`/`AWAITING_SYNONYM`/`AWAITING_PHOTO_CHANGE`) are
dispatched from here too, since they all arrive as the same kind of DM
text message — see `search_text_handler`."""

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start._shared import _SEARCH_SERVICE_ERRORS, _reply_service_down
from nani_pix_bot.commands.dm_start.keyboards import (
    RETRY_CALLBACK_DATA,
    anilist_results_keyboard,
    parse_method_callback_data,
    parse_pick_callback_data,
    shikimori_results_keyboard,
)
from nani_pix_bot.commands.dm_start.manual import _manual_synonyms_step, _manual_title_step
from nani_pix_bot.commands.dm_start.preview import _add_synonym_step, _show_preview
from nani_pix_bot.commands.helpers.scoping import is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import SetupStep
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, settings
from nani_pix_bot.services.search import anilist, shikimori
from nani_pix_bot.services.search.anilist import AniListResult
from nani_pix_bot.services.search.shikimori import ShikimoriResult


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
        logger.debug("Game {}: starter picked identification method {!r}", setup_game.id, source)

    prompt_key = "dm_start.ask_manual_title" if source == "manual" else "dm_start.ask_search"
    await query.edit_message_text(i18n.t(prompt_key, lang))


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

    if setup_step == SetupStep.AWAITING_SYNONYM:
        await _add_synonym_step(message, context, lang, user)
    elif setup_step in (SetupStep.CONFIRMING, SetupStep.AWAITING_PHOTO_CHANGE):
        pass  # only the preview's buttons (or a replacement photo) matter here
    elif source == "manual":
        if awaiting_synonyms:
            await _manual_synonyms_step(message, context, lang, user)
        else:
            await _manual_title_step(message, context, lang, user)
    else:
        await _search_step(message, context, lang, source)


async def _search_step(message, context: ContextTypes.DEFAULT_TYPE, lang: str, source: str) -> None:
    """An AniList/Shikimori search query: search and show a results
    keyboard, or fail back to the method-selection keyboard. Sends a
    "searching" message immediately — the round-trip can take a few
    seconds (more for Shikimori, which goes through the amsterdam proxy)
    — and edits that same message in place with the eventual outcome, so
    the starter gets fast feedback without extra message clutter."""
    status_message = await message.reply_text(i18n.t("dm_start.searching", lang))
    logger.debug("{} search started for query {!r}", source, message.text)

    client = context.bot_data["search_client"]
    try:
        if source == "shikimori":
            shikimori_results = await shikimori.search(client, message.text)
            result_count = len(shikimori_results)
            has_results = bool(shikimori_results)
            keyboard = shikimori_results_keyboard(shikimori_results, lang)
        else:
            anilist_results = await anilist.search(client, message.text)
            result_count = len(anilist_results)
            has_results = bool(anilist_results)
            keyboard = anilist_results_keyboard(anilist_results, lang)
    except _SEARCH_SERVICE_ERRORS:
        logger.exception("{} search failed for query {!r}", source, message.text)
        await _reply_service_down(status_message.edit_text, lang, source)
        return

    logger.debug("{} search for {!r} returned {} results", source, message.text, result_count)
    if not has_results:
        await status_message.edit_text(i18n.t("dm_start.no_results", lang))
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
    await query.answer()

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)

    user = query.from_user
    if user is None:
        return

    resolved = await _resolve_picked_result(query, context, lang)
    if resolved is None:
        return
    source, external_id, result = resolved

    with session_scope(session_factory) as session:
        setup_game = game_service.get_setup_game_for_starter(session, user.id)
        if setup_game is None or setup_game.original_file_id is None:
            return
        game_service.stage_result(setup_game, result, source=source)
        logger.debug("Game {}: staged {} result {}", setup_game.id, source, external_id)
        await _show_preview(context, session, setup_game, lang)

    await query.edit_message_text(i18n.t("dm_start.preview_sent", lang))


async def _resolve_picked_result(
    query, context: ContextTypes.DEFAULT_TYPE, lang: str
) -> tuple[str, int, AniListResult | ShikimoriResult] | None:
    """Handles the "none of these" retry tap and resolves a valid pick to
    its (source, external_id, result) triple. Replies and returns None
    for every already-handled outcome: retry tapped, unparseable
    callback data, the search service erroring, or the id no longer
    existing."""
    if query.data == RETRY_CALLBACK_DATA:
        await query.edit_message_text(i18n.t("dm_start.retry", lang))
        return None

    parsed = parse_pick_callback_data(query.data)
    if parsed is None:
        return None
    source, external_id = parsed

    client = context.bot_data["search_client"]
    try:
        if source == "shikimori":
            result = await shikimori.get_by_id(client, external_id)
        else:
            result = await anilist.get_by_id(client, external_id)
    except _SEARCH_SERVICE_ERRORS:
        logger.exception("{} get_by_id failed for id {}", source, external_id)
        await _reply_service_down(query.edit_message_text, lang, source)
        return None

    if result is None:
        logger.warning("{} id {} picked but no longer found", source, external_id)
        await query.edit_message_text(i18n.t("dm_start.not_found_anymore", lang))
        return None

    return source, external_id, result
