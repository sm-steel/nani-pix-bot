"""Private-chat photo intake + anime-identification (AniList/Shikimori)
search-and-pick flow — see MECHANICS.md's "Starting a game" section.

Deliberately keeps no state in PTB's in-memory `user_data`: which game a
DM is setting up, and which method (AniList/Shikimori) it's using, are
both derived from the DB (`get_setup_game_for_starter`, `Game.source`),
and which search result a tapped button means is re-fetched by id
(`anilist.get_by_id`/`shikimori.get_by_id`) rather than cached — all of
this survives a bot restart mid-setup, which in-memory `user_data`
doesn't (see issue #11).
"""

import re

import httpx
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.keyboards import (
    RETRY_CALLBACK_DATA,
    anilist_results_keyboard,
    method_selection_keyboard,
    parse_method_callback_data,
    parse_pick_callback_data,
    shikimori_results_keyboard,
)
from nani_pix_bot.commands.helpers.membership import is_group_member
from nani_pix_bot.commands.helpers.scoping import is_private_chat
from nani_pix_bot.commands.timeout import schedule_timeout
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import anilist, i18n, settings, shikimori
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import pixelate as pixelate_service

# Raised by anilist.py/shikimori.py on network failure or exhausted
# rate-limit retries — see their _request() helpers.
_SEARCH_SERVICE_ERRORS = (httpx.HTTPError, RuntimeError)

_SERVICE_DISPLAY_NAMES = {"anilist": "AniList", "shikimori": "Shikimori"}

_SYNONYM_SPLIT_RE = re.compile(r"[,\n]")


def _prefer_shikimori(lang: str) -> bool:
    return lang.upper() == "RU"


def _method_prompt_key(*, prefer_shikimori: bool) -> str:
    return (
        "dm_start.pick_method_prompt_shikimori_preferred"
        if prefer_shikimori
        else "dm_start.pick_method_prompt"
    )


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A DM photo starts game setup, if it's this player's turn."""
    message = update.message
    if not is_private_chat(update) or message is None or not message.photo:
        return
    user = update.effective_user
    if user is None:
        return

    group_chat_id = context.bot_data["group_chat_id"]
    session_factory = context.bot_data["session_factory"]

    if not await is_group_member(context.bot, group_chat_id, user.id):
        with session_scope(session_factory) as session:
            lang = settings.get_language(session)
        await message.reply_text(i18n.t("dm_start.not_a_member", lang))
        return

    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        game_service.get_or_create_player(session, user.id, username=user.username)
        if not game_service.can_start(session, user.id):
            await message.reply_text(i18n.t("dm_start.not_your_turn", lang))
            return
        file_id = message.photo[-1].file_id
        game_service.create_setup_game(session, starter_id=user.id, original_file_id=file_id)

    prefer_shikimori = _prefer_shikimori(lang)
    await message.reply_text(
        i18n.t(_method_prompt_key(prefer_shikimori=prefer_shikimori), lang),
        reply_markup=method_selection_keyboard(prefer_shikimori=prefer_shikimori),
    )


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
        source = setup_game.source
        awaiting_synonyms = setup_game.title_english is not None

    if source == "manual":
        if awaiting_synonyms:
            await _manual_synonyms_step(message, context, lang, user)
        else:
            await _manual_title_step(message, context, lang, user)
        return

    client = context.bot_data["search_client"]
    try:
        if source == "shikimori":
            shikimori_results = await shikimori.search(client, message.text)
            has_results = bool(shikimori_results)
            keyboard = shikimori_results_keyboard(shikimori_results)
        else:
            anilist_results = await anilist.search(client, message.text)
            has_results = bool(anilist_results)
            keyboard = anilist_results_keyboard(anilist_results)
    except _SEARCH_SERVICE_ERRORS:
        await _reply_service_down(message.reply_text, lang, source)
        return

    if not has_results:
        await message.reply_text(i18n.t("dm_start.no_results", lang))
        return

    await message.reply_text(i18n.t("dm_start.pick_prompt", lang), reply_markup=keyboard)


async def pick_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The starter tapped a result (or "none of these") from
    search_text_handler's keyboard. A valid pick pixelates the original
    at X10, posts it to the group's game topic, and activates the
    game."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)

    if query.data == RETRY_CALLBACK_DATA:
        await query.edit_message_text(i18n.t("dm_start.retry", lang))
        return

    parsed = parse_pick_callback_data(query.data)
    user = query.from_user
    if parsed is None or user is None:
        return
    source, external_id = parsed

    client = context.bot_data["search_client"]
    try:
        if source == "shikimori":
            result = await shikimori.get_by_id(client, external_id)
        else:
            result = await anilist.get_by_id(client, external_id)
    except _SEARCH_SERVICE_ERRORS:
        await _reply_service_down(query.edit_message_text, lang, source)
        return

    if result is None:
        await query.edit_message_text(i18n.t("dm_start.not_found_anymore", lang))
        return

    with session_scope(session_factory) as session:
        setup_game = game_service.get_setup_game_for_starter(session, user.id)
        if setup_game is None or setup_game.original_file_id is None:
            return
        game_service.stage_result(setup_game, result, source=source)
        caption = i18n.t("dm_start.game_started_caption", lang, starter=user.full_name)
        await _finalize_and_post(context, session, setup_game, caption)

    await query.edit_message_text(i18n.t("dm_start.posted", lang))


async def _manual_title_step(message, context: ContextTypes.DEFAULT_TYPE, lang: str, user) -> None:
    """The first manual-entry text message: the anime's title."""
    title = message.text.strip()
    if not title:
        await message.reply_text(i18n.t("dm_start.ask_manual_title", lang))
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        setup_game = game_service.get_setup_game_for_starter(session, user.id)
        if setup_game is None:
            return
        setup_game.title_english = title

    await message.reply_text(i18n.t("dm_start.ask_synonyms", lang))


async def _manual_synonyms_step(
    message, context: ContextTypes.DEFAULT_TYPE, lang: str, user
) -> None:
    """The second manual-entry text message: at least one synonym. On
    success, stages, activates, and posts — same as an AniList/Shikimori
    pick."""
    synonyms = [s.strip() for s in _SYNONYM_SPLIT_RE.split(message.text) if s.strip()]
    if not synonyms:
        await message.reply_text(i18n.t("dm_start.synonyms_required", lang))
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        setup_game = game_service.get_setup_game_for_starter(session, user.id)
        title = setup_game.title_english if setup_game is not None else None
        if setup_game is None or setup_game.original_file_id is None or title is None:
            return
        game_service.stage_manual_entry(setup_game, title=title, synonyms=synonyms)
        caption = i18n.t("dm_start.game_started_caption", lang, starter=user.full_name)
        await _finalize_and_post(context, session, setup_game, caption)

    await message.reply_text(i18n.t("dm_start.posted", lang))


async def _finalize_and_post(context, session, game: Game, caption: str) -> None:
    """Shared tail end of every identification method (AniList, Shikimori,
    manual): pixelate the original at X10, activate the game, post it to
    the group topic, and schedule the timeout."""
    telegram_file = await context.bot.get_file(game.original_file_id)
    original_bytes = bytes(await telegram_file.download_as_bytearray())
    pixelated = pixelate_service.pixelate(original_bytes, PixelStage.X10)
    game_service.activate_game(session, game)
    await context.bot.send_photo(
        chat_id=context.bot_data["group_chat_id"],
        message_thread_id=context.bot_data["game_topic_id"],
        photo=pixelated,
        caption=caption,
    )
    schedule_timeout(context.job_queue, game)


async def _reply_service_down(send, lang: str, source: str) -> None:
    """Shared failure path for both the search step and the pick step:
    tell the starter the chosen service looks unreachable and hand them
    back the method-selection keyboard rather than leaving them stuck
    with a dead-end SETUP game (see issue #11's orphaned-row incident)."""
    prefer_shikimori = _prefer_shikimori(lang)
    await send(
        i18n.t("dm_start.search_failed", lang, service=_SERVICE_DISPLAY_NAMES[source]),
        reply_markup=method_selection_keyboard(prefer_shikimori=prefer_shikimori),
    )
