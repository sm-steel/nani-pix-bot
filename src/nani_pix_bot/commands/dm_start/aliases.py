"""Suggested extra accepted names (issues #347/#348): once the anime is
identified, the other providers are searched in the background for more
names it goes by, and the creator ticks which ones should also count as
a correct guess, right before the preview. See MECHANICS.md's "Starting
a game".

Never in the way: the search runs as its own task, so no update waits on
it, and any failure, timeout or empty result just means no checkbox step.
On the photo-first path the preview has nothing else to wait for, so the
task posts it itself once the search is done (at most
ALIAS_SEARCH_TIMEOUT later); on /newgame the creator is busy picking a
screenshot meanwhile, and the preview uses whatever is stored by then."""

import asyncio
from collections.abc import Coroutine

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start._shared import (
    _post_preview_album,
    _reject_stale_tap,
    _stage_preview,
    client_for_source,
)
from nani_pix_bot.commands.dm_start.keyboards import (
    ALIASES_DONE_CALLBACK_DATA,
    ALIASES_SKIP_CALLBACK_DATA,
    ALIASES_TOGGLE_PREFIX,
    aliases_keyboard,
)
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import GameStatus, Provider, SetupStep
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, settings
from nani_pix_bot.services.search import aliases

# How long the whole search may take — every provider round-trip
# included — before it's given up as "nothing found".
ALIAS_SEARCH_TIMEOUT = 5.0

# Steps at which a finished search is still in time: the preview hasn't
# been shown yet. Anything later and the results are dropped.
_BEFORE_PREVIEW = frozenset(
    {SetupStep.PICKING_METHOD, SetupStep.PICKING_SCREENSHOT, SetupStep.AWAITING_PHOTO_CHANGE}
)


def _identity(game: Game) -> tuple:
    """What the search was for: a re-identification in the meantime makes
    its results someone else's."""
    return (
        game.source,
        game.anilist_id,
        game.shikimori_id,
        game.tenrai_id,
        game.tmdb_id,
        game.title_english,
        game.title_romaji,
    )


def lookup_for(game: Game) -> aliases.AliasLookup:
    return aliases.AliasLookup(
        source=game.source,
        anilist_id=game.anilist_id,
        shikimori_id=game.shikimori_id,
        tenrai_id=game.tenrai_id,
        title=game.title_english or game.title_romaji,
        accepted=tuple(game_service.match_candidates(game)),
    )


def start_alias_search(
    context: ContextTypes.DEFAULT_TYPE, game_id: int, *, then_preview_in: str | None = None
) -> None:
    """Look for more names for a just-identified game in the background.
    With `then_preview_in` (the photo-first path, where the image is
    already in hand), the task also stages and posts the preview in that
    language once the search is done, whatever it found."""
    _spawn(context, _search_and_store(context, game_id, then_preview_in))


def _spawn(context: ContextTypes.DEFAULT_TYPE, coroutine: Coroutine) -> None:
    # PTB's create_task: errors reach the application's error handler.
    context.application.create_task(coroutine)


async def _find(context: ContextTypes.DEFAULT_TYPE, lookup: aliases.AliasLookup) -> list[str]:
    try:
        clients = aliases.AliasClients(
            anilist=client_for_source(context, Provider.ANILIST),
            shikimori=client_for_source(context, Provider.SHIKIMORI),
            tenrai=client_for_source(context, Provider.TENRAI),
        )
        async with asyncio.timeout(ALIAS_SEARCH_TIMEOUT):
            return await aliases.find_aliases(clients, lookup)
    except TimeoutError:
        logger.warning(
            "alias search took over {seconds:g} s — skipping it", seconds=ALIAS_SEARCH_TIMEOUT
        )
    except Exception:
        # Anything at all: this step is optional and must never cost the
        # creator their preview (see the module docstring).
        logger.exception("alias search failed — skipping it")
    return []


async def _search_and_store(
    context: ContextTypes.DEFAULT_TYPE, game_id: int, preview_lang: str | None
) -> None:
    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        game = session.get(Game, game_id)
        if game is None or game.status != GameStatus.SETUP:
            return
        lookup, identity = lookup_for(game), _identity(game)
    logger.info("looking for more names on other providers", game_id=game_id)
    names = await _find(context, lookup)
    album = None
    with session_scope(session_factory) as session:
        game = session.get(Game, game_id)
        if (
            game is None
            or game.status != GameStatus.SETUP
            or _identity(game) != identity
            or game.setup_step not in _BEFORE_PREVIEW
        ):
            logger.info("alias search finished too late — dropping it", game_id=game_id)
            return
        game.alias_suggestions = [{"text": name, "selected": False} for name in names]
        logger.info(
            "found {count} more name(s): {names}", count=len(names), names=names, game_id=game_id
        )
        if preview_lang is not None and game.original_image is not None:
            album = _stage_preview(session, game, preview_lang)
    if album is not None and preview_lang is not None:
        await _post_preview_album(context, album, preview_lang)


def _toggled(game: Game, data: str) -> bool:
    """Flip one checkbox; False for an index that isn't there."""
    suggestions = [dict(suggestion) for suggestion in game.alias_suggestions or []]
    raw_index = data.removeprefix(ALIASES_TOGGLE_PREFIX)
    if not raw_index.isdecimal() or int(raw_index) >= len(suggestions):
        # Callback data is client-supplied (see keyboards.py's trust-boundary note).
        logger.warning("ignoring alias toggle {data!r}", data=data)
        return False
    suggestion = suggestions[int(raw_index)]
    suggestion["selected"] = not suggestion["selected"]
    game.alias_suggestions = suggestions  # a new list, so the JSON change is saved
    logger.info(
        "{action} suggested name {name!r}",
        action="ticked" if suggestion["selected"] else "unticked",
        name=suggestion["text"],
        game_id=game.id,
    )
    return True


def _finish(game: Game, *, add: bool, lang: str) -> str:
    """Accept the ticked names (or none, on Skip) and close the step."""
    picked = [s["text"] for s in game.alias_suggestions or [] if s["selected"]] if add else []
    game.synonyms = [*(game.synonyms or []), *picked]
    game.alias_suggestions = []
    logger.info("added {count} suggested name(s): {names}", count=len(picked), names=picked)
    if not picked:
        return i18n.t("dm_start.aliases_skipped", lang)
    return i18n.t("dm_start.aliases_added", lang, count=len(picked))


async def aliases_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A checkbox, "Add selected" or "Skip" on the suggested-names step."""
    query = update.callback_query
    if query is None or query.from_user is None:
        return
    data = str(query.data)
    keyboard = outcome = album = None
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        game = game_service.get_setup_game_for_starter(session, query.from_user.id)
        if game is None or game.setup_step != SetupStep.PICKING_ALIASES:
            logger.warning("tapped alias button {data!r} with no names on offer", data=data)
            await _reject_stale_tap(query, lang)
            return
        if data.startswith(ALIASES_TOGGLE_PREFIX):
            if _toggled(game, data):
                keyboard = aliases_keyboard(lang, game.alias_suggestions or [])
        elif data in (ALIASES_DONE_CALLBACK_DATA, ALIASES_SKIP_CALLBACK_DATA):
            outcome = _finish(game, add=data == ALIASES_DONE_CALLBACK_DATA, lang=lang)
            album = _stage_preview(session, game, lang)
    # Block closed and committed above — see _post_preview_album's
    # docstring for why the sends have to happen after.
    await query.answer()
    if keyboard is not None:
        await query.edit_message_reply_markup(reply_markup=keyboard)
    if outcome is not None and album is not None:
        await query.edit_message_text(text=outcome)
        await _post_preview_album(context, album, lang)
