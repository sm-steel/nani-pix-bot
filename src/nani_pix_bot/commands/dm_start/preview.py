"""The confirmation preview shown before a game goes live: an album of
every pixelation stage, a follow-up message with confirm/change-image/
research/add-synonym buttons, and (on confirm) the actual post to the
group topic. See MECHANICS.md's "Starting a game" section."""

from dataclasses import dataclass

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start._shared import (
    _SYNONYM_SPLIT_RE,
    _method_prompt_key,
    _post_preview_album,
    _prefer_shikimori,
    _stage_preview,
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
    reply_with_source_menu,
    resume_screenshot_gallery,
    send_fallback_notice,
    source_menu_for,
    stage_fallback,
)
from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import timers as timeout_module
from nani_pix_bot.models.enums import DISCOURAGED_ALGORITHMS, PixelAlgorithm, SetupStep
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, settings
from nani_pix_bot.services import pixelate as pixelate_service
from nani_pix_bot.services.settings import stage_config


@dataclass(frozen=True)
class _FirstStagePost:
    """What the confirm tap's announcement needs, captured while its
    session was still open — post_current_image runs after that session
    (and its commit) has already closed."""

    photo: bytes
    caption: str


def _activate_and_stage_first_post(
    session, context: ContextTypes.DEFAULT_TYPE, game: Game, lang: str, starter_name: str
) -> _FirstStagePost:
    """Shared tail end of every identification method (AniList, Shikimori,
    manual): pixelate the original at the first (blockiest) stage, activate
    the game, and schedule its timers — canceling the setup-abandon timer
    that's been running since the photo was first sent (issue #21). The
    actual group post happens after the caller's session commits (see
    post_current_image's docstring) — activation must not be rolled back
    by a Telegram hiccup on that send.

    Builds the group caption here rather than taking it prebuilt, because
    it names stage 1 and that stage's wrong-guess budget — both of which
    come from the stage config this function is already loading for the
    pixelation width. Read straight off STAGE_ORDER[0] rather than through
    game_service.stage_progress(), which needs an already-ACTIVE game;
    activate_game() only runs a couple of lines below."""
    timeout_module.cancel_setup_abandon(context.job_queue, game.id)
    if game.original_image is None:
        raise RuntimeError("game.original_image is None in _activate_and_stage_first_post")
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
    timeout_module.schedule_timeout(context.job_queue, game)
    timeout_module.schedule_inactivity_timers(context.job_queue, game)
    return _FirstStagePost(photo=pixelated, caption=caption)


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
        album = _stage_preview(session, setup_game, lang)
    # Block closed and committed above — see _post_preview_album's
    # docstring for why the send has to happen after.
    await _post_preview_album(context, album, lang)


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
    if await _handle_posting_tap(context, session_factory, query, user):
        return

    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        setup_game = game_service.get_setup_game_for_starter(session, user.id)
        if setup_game is None:
            return

        if query.data == PREVIEW_CHANGE_IMAGE_CALLBACK_DATA:
            await _preview_change_image(query, setup_game, lang)
        elif query.data == PREVIEW_CHANGE_IMAGE_UPLOAD_CALLBACK_DATA:
            await _preview_change_image_upload(query, setup_game, lang)
        elif query.data == PREVIEW_RESEARCH_CALLBACK_DATA:
            await _preview_research(query, setup_game, lang, context)
        elif query.data == PREVIEW_ADD_SYNONYM_CALLBACK_DATA:
            await _preview_add_synonym(query, setup_game, lang)
        elif query.data == PREVIEW_PIXEL_ALGORITHM_CALLBACK_DATA:
            await _preview_pixel_algorithm(query, setup_game, lang)
        elif query.data == PREVIEW_PIXEL_ALGORITHM_BACK_CALLBACK_DATA:
            await _preview_pixel_algorithm_back(query, setup_game, lang)


async def _handle_posting_tap(
    context: ContextTypes.DEFAULT_TYPE, session_factory, query, user
) -> bool:
    """The taps that *send* something — a group post, a gallery, a fresh
    preview album. Each owns its own session so its mutation commits
    before the send is attempted (see _handle_confirm_tap and
    post_current_image's docstring), which is why they can't share
    preview_callback_handler's session with the branches that only edit
    the message they were tapped from.

    Returns True if this tap was one of them and has been handled."""
    data = str(query.data)
    if data == PREVIEW_CONFIRM_CALLBACK_DATA:
        lang = await _handle_confirm_tap(context, session_factory, user)
        if lang is not None:
            await query.edit_message_text(text=i18n.t("dm_start.posted", lang))
        return True
    if data == PREVIEW_CHANGE_IMAGE_PICK_SCREENSHOT_CALLBACK_DATA:
        await _handle_change_image_pick_screenshot_tap(context, session_factory, query, user)
        return True
    if data.startswith(PREVIEW_PIXEL_ALGORITHM_PICK_PREFIX):
        await _handle_pixel_algorithm_pick_tap(context, session_factory, query, user)
        return True
    return False


async def _handle_confirm_tap(
    context: ContextTypes.DEFAULT_TYPE, session_factory, user
) -> str | None:
    """Returns the group's language if a game was actually confirmed (so
    the caller can report the outcome), or None for a stale tap. Split
    out from preview_callback_handler's shared session — activating the
    game and posting stage 1 must commit before the announcement is
    attempted (see post_current_image's docstring), so this branch can't
    share a session with the other five, which don't post anything."""
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        setup_game = game_service.get_setup_game_for_starter(session, user.id)
        if setup_game is None:
            return None
        logger.debug("Game {}: confirmed from preview", setup_game.id)
        first_stage_post = _activate_and_stage_first_post(
            session, context, setup_game, lang, user.full_name
        )
    # Block closed and committed above — activation is durable now
    # regardless of whether the announcement below actually reaches the
    # group (see post_current_image's docstring).
    await timeout_module.post_current_image(
        context, session_factory, photo=first_stage_post.photo, caption=first_stage_post.caption
    )
    return lang


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


async def _handle_change_image_pick_screenshot_tap(
    context: ContextTypes.DEFAULT_TYPE, session_factory, query, user
) -> None:
    """Split out from preview_callback_handler's shared session: a
    ScreenshotFailure outcome re-arms the setup_step/screenshot_picker_provider
    columns (via stage_fallback), which must commit before the fallback
    notice is attempted (see post_current_image's docstring) — the same
    reason _handle_confirm_tap can't share a session with the other
    branches either.

    Note: resume_screenshot_gallery's own success path still sends its
    gallery page from inside the session below (it owns that network
    call internally, not this function) — narrower in scope than the
    fallback-notice fix this split makes, and not addressed here."""
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        setup_game = game_service.get_setup_game_for_starter(session, user.id)
        if setup_game is None:
            return
        logger.debug("Game {}: pick-a-different-screenshot requested from preview", setup_game.id)
        # resume_screenshot_gallery owns the setup_step transition itself
        # and returns either a plain reply key or a ScreenshotFailure —
        # the latter puts the starter back on the source menu rather
        # than leaving this message buttonless (see screenshots.py's
        # reply_with_source_menu).
        outcome = await resume_screenshot_gallery(context, setup_game, lang)
        notice = None
        if isinstance(outcome, ScreenshotFailure):
            notice = stage_fallback(setup_game, outcome)
    if isinstance(outcome, ScreenshotFailure):
        if notice is None:
            raise RuntimeError("stage_fallback was not called for a ScreenshotFailure outcome")
        # Block closed and committed above — see stage_fallback's
        # docstring for why the send has to happen after.
        await send_fallback_notice(query.edit_message_text, notice, lang)
        return
    if outcome == NO_SOURCE_PROMPT_KEY:
        # A stale tap: the source this gallery would have resumed is
        # gone, so no gallery (and therefore no buttons) goes out
        # alongside this message. It has to carry the source menu
        # itself, plain — there is no provider at fault to flag — or the
        # starter reads "Where should I get a screenshot from?" with
        # nothing to tap (MECHANICS.md's "When a provider fails").
        logger.warning(
            "Game {}: stale pick-a-screenshot tap, re-offering the source menu", setup_game.id
        )
        await reply_with_source_menu(
            query.edit_message_text, source_menu_for(setup_game, None), lang, outcome
        )
        return
    await query.edit_message_text(text=i18n.t(outcome, lang))


async def _preview_research(
    query, game: Game, lang: str, context: ContextTypes.DEFAULT_TYPE
) -> None:
    logger.debug("Game {}: re-search requested from preview", game.id)
    # An API-sourced screenshot is cleared here (see
    # clear_screenshot_selection's docstring) since re-searching might
    # pick a different anime entirely — a genuine upload is left alone,
    # unchanged from before ticket 9.
    clear_screenshot_selection(game)
    game.setup_step = SetupStep.PICKING_METHOD
    prefer_shikimori = _prefer_shikimori(lang)
    mal_configured = bool(context.bot_data.get("mal_client_id"))
    await query.edit_message_text(
        text=i18n.t(_method_prompt_key(prefer_shikimori=prefer_shikimori), lang),
        reply_markup=method_selection_keyboard(
            prefer_shikimori=prefer_shikimori, lang=lang, mal_configured=mal_configured
        ),
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


async def _handle_pixel_algorithm_pick_tap(
    context: ContextTypes.DEFAULT_TYPE, session_factory, query, user
) -> None:
    """Store the pick and re-show the whole preview, so the starter sees
    every stage under the new algorithm rather than taking the change on
    trust.

    Split out of preview_callback_handler's shared session for the same
    reason _handle_confirm_tap is: this branch posts a fresh album, and
    both the stored pick and setup_step have to commit before that send
    is attempted (see _post_preview_album's docstring). The submenu
    message loses its keyboard first — the fresh preview brings its own,
    and two live keyboards for one game would be ambiguous."""
    value = str(query.data).removeprefix(PREVIEW_PIXEL_ALGORITHM_PICK_PREFIX)
    try:
        algorithm = PixelAlgorithm(value)
    except ValueError:
        # Callback data is client-supplied; this branch matches on prefix
        # only (see keyboards.py's trust-boundary note).
        logger.warning("Ignoring unknown pixelation algorithm {!r} from user {}", value, user.id)
        return

    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        setup_game = game_service.get_setup_game_for_starter(session, user.id)
        if setup_game is None:
            return
        setup_game.pixel_algorithm = algorithm
        logger.info("Game {}: pixelation algorithm set to {}", setup_game.id, algorithm.value)
        album = _stage_preview(session, setup_game, lang)
    # Block closed and committed above — see _post_preview_album's
    # docstring for why the sends have to happen after.
    await query.edit_message_text(
        text=i18n.t("dm_start.pixel_algorithm_updated", lang, name=algorithm_name(algorithm, lang))
    )
    await _post_preview_album(context, album, lang)
