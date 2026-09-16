"""The confirmation preview shown before a game goes live: an album of
every pixelation stage, a follow-up message with confirm/change-image/
research/add-synonym buttons, and (on confirm) the actual post to the
group topic. See MECHANICS.md's "Starting a game" section."""

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start._shared import (
    _SYNONYM_SPLIT_RE,
    _method_prompt_key,
    _prefer_shikimori,
    _show_preview,
)
from nani_pix_bot.commands.dm_start.keyboards import (
    PREVIEW_ADD_SYNONYM_CALLBACK_DATA,
    PREVIEW_CHANGE_IMAGE_CALLBACK_DATA,
    PREVIEW_CHANGE_IMAGE_PICK_SCREENSHOT_CALLBACK_DATA,
    PREVIEW_CHANGE_IMAGE_UPLOAD_CALLBACK_DATA,
    PREVIEW_CONFIRM_CALLBACK_DATA,
    PREVIEW_PIXEL_ALGORITHM_BACK_CALLBACK_DATA,
    PREVIEW_PIXEL_ALGORITHM_CALLBACK_DATA,
    PREVIEW_PIXEL_ALGORITHM_PICK_PREFIX,
    PREVIEW_RESEARCH_CALLBACK_DATA,
    algorithm_name,
    change_image_keyboard,
    method_selection_keyboard,
    pixel_algorithm_keyboard,
    preview_keyboard,
)
from nani_pix_bot.commands.dm_start.screenshots import (
    NO_SOURCE_PROMPT_KEY,
    ScreenshotFailure,
    clear_screenshot_selection,
    reply_fallback,
    reply_with_source_menu,
    resume_screenshot_gallery,
    source_menu_for,
)
from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import timers as timeout_module
from nani_pix_bot.models.enums import DISCOURAGED_ALGORITHMS, PixelAlgorithm, SetupStep
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, settings
from nani_pix_bot.services import pixelate as pixelate_service
from nani_pix_bot.services.settings import stage_config


async def _finalize_and_post(context, session, game: Game, lang: str, starter_name: str) -> None:
    """Shared tail end of every identification method (AniList, Shikimori,
    manual): pixelate the original at the first (blockiest) stage, activate the game, post it to
    the group topic, and schedule the timeout — canceling the setup-abandon
    timer that's been running since the photo was first sent (issue #21).

    Builds the group caption here rather than taking it prebuilt, because
    it names stage 1 and that stage's wrong-guess budget — both of which
    come from the stage config this function is already loading for the
    pixelation width. Read straight off STAGE_ORDER[0] rather than through
    game_service.stage_progress(), which needs an already-ACTIVE game;
    activate_game() only runs a couple of lines below."""
    timeout_module.cancel_setup_abandon(context.job_queue, game.id)
    if game.original_image is None:
        raise RuntimeError("game.original_image is None in _finalize_and_post")
    original_bytes = game.original_image
    first_stage = game_service.STAGE_ORDER[0]
    first_stage_settings = stage_config.get_stage_config(session, first_stage)
    pixelated = pixelate_service.pixelate(
        original_bytes,
        first_stage_settings.target_width,
        game.pixel_algorithm,
    )
    caption = i18n.t(
        "dm_start.game_started_caption",
        lang,
        starter=starter_name,
        stage=1,
        total=len(game_service.STAGE_ORDER),
        remaining=first_stage_settings.wrong_guess_limit,
        limit=first_stage_settings.wrong_guess_limit,
    )
    game_service.activate_game(session, game)
    await timeout_module.post_current_image(context, session, photo=pixelated, caption=caption)
    timeout_module.schedule_timeout(context.job_queue, game)
    timeout_module.schedule_inactivity_timers(context.job_queue, game)


async def _add_synonym_step(message, context: ContextTypes.DEFAULT_TYPE, lang: str, user) -> None:
    """A text message sent after tapping the preview's "Add a synonym"
    button: appends it (or several, comma/newline-separated) and
    re-shows the preview."""
    extra = [s.strip() for s in _SYNONYM_SPLIT_RE.split(message.text) if s.strip()]
    if not extra:
        await message.reply_text(i18n.t("dm_start.synonyms_required", lang))
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        setup_game = game_service.get_setup_game_for_starter(session, user.id)
        if setup_game is None:
            return
        setup_game.synonyms = [*(setup_game.synonyms or []), *extra]
        logger.debug("Game {}: appended {} extra synonym(s)", setup_game.id, len(extra))
        await _show_preview(context, session, setup_game, lang)


async def preview_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """One of the confirmation preview's four buttons was tapped. The
    buttons live on the plain-text follow-up message _show_preview sends
    after the album (sendMediaGroup can't carry a keyboard itself), so
    every branch here edits that message's text, not a photo caption."""
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
        setup_game = game_service.get_setup_game_for_starter(session, user.id)
        if setup_game is None:
            return

        if query.data == PREVIEW_CONFIRM_CALLBACK_DATA:
            await _preview_confirm(context, session, setup_game, lang, user.full_name)
            await query.edit_message_text(text=i18n.t("dm_start.posted", lang))
        elif query.data == PREVIEW_CHANGE_IMAGE_CALLBACK_DATA:
            await _preview_change_image(query, setup_game, lang)
        elif query.data == PREVIEW_CHANGE_IMAGE_UPLOAD_CALLBACK_DATA:
            await _preview_change_image_upload(query, setup_game, lang)
        elif query.data == PREVIEW_CHANGE_IMAGE_PICK_SCREENSHOT_CALLBACK_DATA:
            await _preview_change_image_pick_screenshot(context, query, setup_game, lang)
        elif query.data == PREVIEW_RESEARCH_CALLBACK_DATA:
            await _preview_research(query, setup_game, lang)
        elif query.data == PREVIEW_ADD_SYNONYM_CALLBACK_DATA:
            await _preview_add_synonym(query, setup_game, lang)
        elif query.data == PREVIEW_PIXEL_ALGORITHM_CALLBACK_DATA:
            await _preview_pixel_algorithm(query, setup_game, lang)
        elif query.data == PREVIEW_PIXEL_ALGORITHM_BACK_CALLBACK_DATA:
            await _preview_pixel_algorithm_back(query, setup_game, lang)
        elif query.data.startswith(PREVIEW_PIXEL_ALGORITHM_PICK_PREFIX):
            await _preview_pixel_algorithm_pick(context, session, query, setup_game, lang)


async def _preview_confirm(context, session, game: Game, lang: str, starter_name: str) -> None:
    logger.debug("Game {}: confirmed from preview", game.id)
    await _finalize_and_post(context, session, game, lang, starter_name)


async def _preview_change_image(query, game: Game, lang: str) -> None:
    """A genuine upload (screenshot_source unset) goes straight to
    asking for a new photo, exactly as before ticket 9. An API-sourced
    screenshot instead offers a choice — upload one after all, or pick
    a different screenshot from the same provider — via
    _preview_change_image_upload/_preview_change_image_pick_screenshot."""
    logger.debug("Game {}: change-image requested from preview", game.id)
    if game.screenshot_source is not None:
        await query.edit_message_text(
            text=i18n.t("dm_start.pick_new_image_source_prompt", lang),
            reply_markup=change_image_keyboard(lang),
        )
        return
    game.setup_step = SetupStep.AWAITING_PHOTO_CHANGE
    await query.edit_message_text(text=i18n.t("dm_start.ask_new_photo", lang))


async def _preview_change_image_upload(query, game: Game, lang: str) -> None:
    logger.debug("Game {}: upload-a-new-photo requested from preview", game.id)
    game.setup_step = SetupStep.AWAITING_PHOTO_CHANGE
    await query.edit_message_text(text=i18n.t("dm_start.ask_new_photo", lang))


async def _preview_change_image_pick_screenshot(context, query, game: Game, lang: str) -> None:
    logger.debug("Game {}: pick-a-different-screenshot requested from preview", game.id)
    # resume_screenshot_gallery owns the setup_step transition itself and
    # returns either a plain reply key or a ScreenshotFailure — the latter puts
    # the starter back on the source menu rather than leaving this
    # message buttonless (see screenshots.py's reply_with_source_menu).
    outcome = await resume_screenshot_gallery(context, game, lang)
    if isinstance(outcome, ScreenshotFailure):
        await reply_fallback(query.edit_message_text, game, lang, outcome)
        return
    if outcome == NO_SOURCE_PROMPT_KEY:
        # A stale tap: the source this gallery would have resumed is
        # gone, so no gallery (and therefore no buttons) goes out
        # alongside this message. It has to carry the source menu
        # itself, plain — there is no provider at fault to flag — or the
        # starter reads "Where should I get a screenshot from?" with
        # nothing to tap (MECHANICS.md's "When a provider fails").
        logger.warning("Game {}: stale pick-a-screenshot tap, re-offering the source menu", game.id)
        await reply_with_source_menu(
            query.edit_message_text, source_menu_for(game, None), lang, outcome
        )
        return
    await query.edit_message_text(text=i18n.t(outcome, lang))


async def _preview_research(query, game: Game, lang: str) -> None:
    logger.debug("Game {}: re-search requested from preview", game.id)
    # An API-sourced screenshot is cleared here (see
    # clear_screenshot_selection's docstring) since re-searching might
    # pick a different anime entirely — a genuine upload is left alone,
    # unchanged from before ticket 9.
    clear_screenshot_selection(game)
    game.setup_step = SetupStep.PICKING_METHOD
    prefer_shikimori = _prefer_shikimori(lang)
    await query.edit_message_text(
        text=i18n.t(_method_prompt_key(prefer_shikimori=prefer_shikimori), lang),
        reply_markup=method_selection_keyboard(prefer_shikimori=prefer_shikimori, lang=lang),
    )


async def _preview_add_synonym(query, game: Game, lang: str) -> None:
    logger.debug("Game {}: add-synonym requested from preview", game.id)
    game.setup_step = SetupStep.AWAITING_SYNONYM
    await query.edit_message_text(text=i18n.t("dm_start.ask_extra_synonym", lang))


def _algorithm_options(lang: str) -> str:
    """The submenu's body text: every algorithm with a one-line
    description, the discouraged one saying so in words rather than only
    as a button glyph. Which one is current is shown on the buttons
    instead, so this text is the same whatever is selected."""
    lines = []
    for algorithm in PixelAlgorithm:
        description = i18n.t(f"dm_start.algo_desc_{algorithm.value}", lang)
        if algorithm in DISCOURAGED_ALGORITHMS:
            description += i18n.t("dm_start.pixel_algorithm_worst_note", lang)
        lines.append(
            i18n.t(
                "dm_start.pixel_algorithm_option",
                lang,
                name=algorithm_name(algorithm, lang),
                description=description,
            )
        )
    return "\n".join(lines)


async def _preview_pixel_algorithm(query, game: Game, lang: str) -> None:
    logger.debug("Game {}: pixelation submenu opened from preview", game.id)
    await query.edit_message_text(
        text=i18n.t(
            "dm_start.pixel_algorithm_prompt",
            lang,
            options=_algorithm_options(lang),
        ),
        reply_markup=pixel_algorithm_keyboard(lang, game.pixel_algorithm),
        parse_mode="HTML",
    )


async def _preview_pixel_algorithm_back(query, game: Game, lang: str) -> None:
    """Leave the submenu without changing anything — back to the same
    prompt and buttons the album's follow-up message started with."""
    logger.debug("Game {}: pixelation submenu dismissed", game.id)
    await query.edit_message_text(
        text=i18n.t("dm_start.preview_confirm_prompt", lang),
        reply_markup=preview_keyboard(lang, game.pixel_algorithm),
    )


async def _preview_pixel_algorithm_pick(context, session, query, game: Game, lang: str) -> None:
    """Store the pick and re-show the whole preview, so the starter sees
    every stage under the new algorithm rather than taking the change on
    trust. The submenu message loses its keyboard first — _show_preview
    posts a fresh album and a fresh button message, and two live
    keyboards for one game would be ambiguous."""
    value = query.data.removeprefix(PREVIEW_PIXEL_ALGORITHM_PICK_PREFIX)
    try:
        algorithm = PixelAlgorithm(value)
    except ValueError:
        # Callback data is client-supplied; every handler here matches on
        # prefix only (see keyboards.py's trust-boundary note).
        logger.warning("Game {}: ignoring unknown pixelation algorithm {!r}", game.id, value)
        return

    game.pixel_algorithm = algorithm
    logger.info("Game {}: pixelation algorithm set to {}", game.id, algorithm.value)
    await query.edit_message_text(
        text=i18n.t("dm_start.pixel_algorithm_updated", lang, name=algorithm_name(algorithm, lang))
    )
    await _show_preview(context, session, game, lang)
