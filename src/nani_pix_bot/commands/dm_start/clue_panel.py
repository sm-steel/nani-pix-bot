"""The setup preview's clue screenshot panel (issue #452): where the
starter of a player-started game picks where the shop's "Extra screenshot"
clues come from (Random, Author's or Both) and builds the author's list —
uploads and removals here, gallery picks in clue_gallery.py. It edits the
preview's buttons message in place, like the preview's other sub-menus,
and every tap re-reads the SETUP game. Nothing here touches the main
screenshot; nothing can change once the game is confirmed. See
MECHANICS.md's "Starting a game" and "Clue shop"."""

from collections.abc import Callable
from dataclasses import dataclass

from loguru import logger
from sqlalchemy.orm import Session
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start import clue_screens, preview
from nani_pix_bot.commands.dm_start._shared import (
    _confirming_screen,
    _reject_stale_tap,
    clue_upload_screen,
)
from nani_pix_bot.commands.dm_start.clue_keyboards import (
    CLUE_PANEL_BACK_CALLBACK_DATA,
    CLUE_PANEL_MODE_PREFIX,
    CLUE_PANEL_REMOVE_CALLBACK_DATA,
    CLUE_PANEL_REMOVE_PREFIX,
    CLUE_PANEL_SOURCE_CALLBACK_DATA,
    CLUE_PANEL_START_RANDOM_CALLBACK_DATA,
    CLUE_PANEL_UPLOAD_CALLBACK_DATA,
    clue_back_keyboard,
    clue_remove_keyboard,
    clue_source_keyboard,
    parse_clue_mode,
    parse_clue_remove,
)
from nani_pix_bot.commands.dm_start.clue_screens import Screen
from nani_pix_bot.commands.dm_start.keyboards import CLUE_PANEL_OPEN_CALLBACK_DATA
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import ClueScreenshotMode, SetupStep
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, settings
from nani_pix_bot.services.clues import author_list, shop


@dataclass(frozen=True)
class _Tap:
    """What a panel action works from, inside the handler's session."""

    session: Session
    game: Game
    lang: str
    data: str


def _panel(tap: _Tap, note: str | None = None) -> Screen:
    tap.game.setup_step = SetupStep.CONFIRMING
    return clue_screens.panel_screen(tap.session, tap.game, tap.lang, note)


def _open(tap: _Tap) -> Screen:
    logger.info(
        "opened the clue screenshot panel (mode {mode})", mode=tap.game.clue_screenshot_mode
    )
    return _panel(tap)


def _set_mode(tap: _Tap) -> Screen:
    mode = parse_clue_mode(tap.data)  # a forged one is logged there
    if mode is not None:
        tap.game.clue_screenshot_mode = mode
        logger.info("set the clue screenshot mode to {mode}", mode=mode.value)
    return _panel(tap)


def _upload(tap: _Tap) -> Screen:
    logger.info("chose to upload a clue screenshot")
    tap.game.setup_step = SetupStep.AWAITING_CLUE_PHOTO
    return clue_upload_screen(tap.lang)


def _remove_menu(tap: _Tap) -> Screen:
    listed = author_list.entries(tap.session, tap.game.id)
    logger.info("opened the clue screenshot removal list ({count} entries)", count=len(listed))
    if not listed:
        return _panel(tap)
    tap.game.setup_step = SetupStep.CONFIRMING
    return i18n.t("dm_start.clue_remove_prompt", tap.lang), clue_remove_keyboard(tap.lang, listed)


def _remove(tap: _Tap) -> Screen:
    entry_id = parse_clue_remove(tap.data)
    removed = entry_id is not None and author_list.remove(tap.session, tap.game.id, entry_id)
    return _panel(tap, i18n.t("dm_start.clue_removed", tap.lang) if removed else None)


def _source_menu(tap: _Tap) -> Screen:
    """Where a clue screenshot can be picked from: Shikimori/TMDB only, as
    the shop (Tenrai's pictures are promotional art that can show the
    title)."""
    providers = shop.clue_screenshot_providers(tap.game)
    logger.info(
        "opened the clue screenshot sources ({providers})",
        providers=", ".join(providers) or "none",
    )
    tap.game.setup_step = SetupStep.CONFIRMING
    if not providers:
        return i18n.t("dm_start.clue_no_source", tap.lang), clue_back_keyboard(tap.lang)
    return (
        i18n.t("dm_start.clue_source_prompt", tap.lang),
        clue_source_keyboard(tap.lang, providers),
    )


def _back(tap: _Tap) -> Screen:
    logger.info("closed the clue screenshot panel")
    tap.game.setup_step = SetupStep.CONFIRMING
    return _confirming_screen(tap.game, tap.lang, None)


_EXACT: dict[str, Callable[[_Tap], Screen]] = {
    CLUE_PANEL_OPEN_CALLBACK_DATA: _open,
    CLUE_PANEL_UPLOAD_CALLBACK_DATA: _upload,
    CLUE_PANEL_REMOVE_CALLBACK_DATA: _remove_menu,
    CLUE_PANEL_SOURCE_CALLBACK_DATA: _source_menu,
    CLUE_PANEL_BACK_CALLBACK_DATA: _back,
}
_PREFIXED: tuple[tuple[str, Callable[[_Tap], Screen]], ...] = (
    (CLUE_PANEL_MODE_PREFIX, _set_mode),
    (CLUE_PANEL_REMOVE_PREFIX, _remove),
)


def _action(data: str) -> Callable[[_Tap], Screen] | None:
    if data in _EXACT:
        return _EXACT[data]
    return next((action for prefix, action in _PREFIXED if data.startswith(prefix)), None)


async def clue_panel_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A `cluepanel:` button. A tap with no SETUP game left, or from a
    setup that has moved on from the preview, only drops the buttons."""
    query = update.callback_query
    if query is None or query.data is None or query.from_user is None:
        return
    if query.data == CLUE_PANEL_START_RANDOM_CALLBACK_DATA:
        await _start_anyway(context, query)
        return
    action = _action(query.data)
    if action is None:
        # Callback data is client-supplied (see keyboards.py's trust-boundary note).
        logger.warning("ignoring unknown clue panel button {data!r}", data=query.data)
        await query.answer()
        return
    screen: Screen | None = None
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        game = game_service.get_setup_game_for_starter(session, query.from_user.id)
        if game is not None and clue_screens.accepts_panel_taps(game):
            screen = action(_Tap(session, game, lang, query.data))
        elif game is not None:
            logger.warning(
                "tapped {data!r} on an old clue panel (setup step {step}) — dropping its buttons",
                data=query.data,
                step=game.setup_step,
            )
    if game is None:
        await _reject_stale_tap(query, lang)
    else:
        await query.answer()
    if screen is None:
        await query.edit_message_reply_markup(reply_markup=None)
        return
    text, markup = screen
    await query.edit_message_text(text=text, reply_markup=markup)


async def _start_anyway(context: ContextTypes.DEFAULT_TYPE, query) -> None:
    """ "Start anyway" on Confirm's empty-list warning (issue #453): Random
    mode, then the normal Confirm, season gate included. The Confirm itself
    refuses a setup that moved on since the warning."""
    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        game = game_service.get_setup_game_for_starter(session, query.from_user.id)
        if game is not None and game.setup_step == SetupStep.CONFIRMING and not game.hard_mode:
            game.clue_screenshot_mode = ClueScreenshotMode.RANDOM
            logger.info("chose Start anyway with no clue screenshots — starting in Random mode")
    await preview._handle_gated_confirm(context, session_factory, query, query.from_user)


async def accept_clue_photo(
    context: ContextTypes.DEFAULT_TYPE, session_factory, user, image_bytes: bytes
) -> bool:
    """A DM photo after the panel's "Upload my own": it joins the author's
    list (never the main screenshot), and the panel comes back with what
    happened. Returns whether the photo was this (photo_handler then does
    nothing else with it)."""
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        game = game_service.get_setup_game_for_starter(session, user.id)
        if game is None or game.setup_step != SetupStep.AWAITING_CLUE_PHOTO:
            return False
        try:
            note = clue_screens.add_note(
                author_list.add(session, game, author_list.NewEntry(image_bytes)), lang
            )
        except ValueError:
            logger.opt(exception=True).warning(
                "sent a clue screenshot that isn't a readable image — not added"
            )
            note = i18n.t("dm_start.clue_not_an_image", lang)
        game.setup_step = SetupStep.CONFIRMING
        text, markup = clue_screens.panel_screen(session, game, lang, note)
    await context.bot.send_message(chat_id=user.id, text=text, reply_markup=markup)
    return True
