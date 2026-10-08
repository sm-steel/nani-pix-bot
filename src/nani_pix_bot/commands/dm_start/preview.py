"""The confirmation preview shown before a game goes live: an album of
every pixelation stage, a follow-up message with confirm/change-image/
research/add-synonym buttons, and (on confirm) the actual post to the
group topic. See MECHANICS.md's "Starting a game" section."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from loguru import logger
from sqlalchemy.orm import Session
from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start._shared import (
    _SYNONYM_SPLIT_RE,
    _method_keyboard,
    _method_prompt_key,
    _post_preview_album,
    _prefer_shikimori,
    _reject_stale_tap,
    _stage_preview,
)
from nani_pix_bot.commands.dm_start.keyboards import (
    BOUNTY_PRESETS,
    PREVIEW_ADD_SYNONYM_CALLBACK_DATA,
    PREVIEW_BOUNTY_BACK_CALLBACK_DATA,
    PREVIEW_BOUNTY_CALLBACK_DATA,
    PREVIEW_BOUNTY_PICK_PREFIX,
    PREVIEW_CHANGE_IMAGE_CALLBACK_DATA,
    PREVIEW_CHANGE_IMAGE_PICK_SCREENSHOT_CALLBACK_DATA,
    PREVIEW_CHANGE_IMAGE_UPLOAD_CALLBACK_DATA,
    PREVIEW_CONFIRM_CALLBACK_DATA,
    PREVIEW_PIXEL_ALGORITHM_BACK_CALLBACK_DATA,
    PREVIEW_PIXEL_ALGORITHM_CALLBACK_DATA,
    PREVIEW_PIXEL_ALGORITHM_PICK_PREFIX,
    PREVIEW_RESEARCH_CALLBACK_DATA,
    algorithm_name,
    bounty_keyboard,
    change_image_keyboard,
    pixel_algorithm_keyboard,
    preview_keyboard,
)
from nani_pix_bot.commands.dm_start.screenshots import (
    NO_SOURCE_PROMPT_KEY,
    ScreenshotFailure,
    clear_screenshot_selection,
    reply_with_source_menu,
    send_fallback_notice,
    send_gallery_resume,
    source_menu_for,
    stage_fallback,
    stage_gallery_resume,
)
from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import timers as timeout_module
from nani_pix_bot.models.enums import DISCOURAGED_ALGORITHMS, PixelAlgorithm, SetupStep
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, settings
from nani_pix_bot.services import pixelate as pixelate_service
from nani_pix_bot.services.economy import bounty, earning, wallet
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
    caption += game_service.game_id_line(game.id, lang)
    game_service.activate_game(session, game)
    prompt_bonus = earning.award_prompt_start(session, game)
    if prompt_bonus:
        caption += "\n" + i18n.t(
            "economy.prompt_turn_bonus", lang, name=starter_name, amount=prompt_bonus
        )
    timeout_module.schedule_timeout(context.job_queue, game)
    timeout_module.schedule_inactivity_timers(context.job_queue, game)
    return _FirstStagePost(photo=pixelated, caption=caption)


async def _add_synonym_step(message, context: ContextTypes.DEFAULT_TYPE, lang: str, user) -> None:
    """A text message sent after tapping the preview's "Add a synonym"
    button: appends it (or several, comma/newline-separated) and
    re-shows the preview."""
    extra = [s.strip() for s in _SYNONYM_SPLIT_RE.split(message.text) if s.strip()]
    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        setup_game = game_service.get_setup_game_for_starter(session, user.id)
        if setup_game is None:
            logger.warning("typed an extra synonym with no SETUP game left — ignoring")
            return
        if not extra:
            logger.info("sent no usable synonym — asking again", game_id=setup_game.id)
            await message.reply_text(i18n.t("dm_start.synonyms_required", lang))
            return
        setup_game.synonyms = [*(setup_game.synonyms or []), *extra]
        logger.info("added synonym(s) {synonyms!r}", synonyms=extra, game_id=setup_game.id)
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
    user = query.from_user
    session_factory = context.bot_data["session_factory"]
    if user is not None and query.data.startswith(PREVIEW_BOUNTY_PICK_PREFIX):
        # Answers the query itself: a refusal is an alert, and Telegram
        # takes only one answer per callback query.
        await _handle_bounty_pick_tap(session_factory, query, user)
        return
    await query.answer()
    if user is None:
        return

    if not await _handle_posting_tap(context, session_factory, query, user):
        await _handle_branch_tap(context, session_factory, query, user)


async def _handle_branch_tap(
    context: ContextTypes.DEFAULT_TYPE, session_factory, query, user
) -> None:
    """A _BRANCHES button: the branch prepares its edit inside the session,
    and the edit goes out only once that session has committed — whatever
    step the branch moved the game to is saved before the message that
    shows it, so a timed-out edit (which may well have landed) can't roll
    it back (issue #292; see post_current_image's docstring)."""
    branch = _BRANCHES.get(query.data)
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        setup_game = game_service.get_setup_game_for_starter(session, user.id)
        if setup_game is None:
            logger.warning(
                "tapped preview button {data!r} with no SETUP game left — ignoring",
                data=query.data,
            )
            return
        edit = branch(_Tap(session, setup_game, lang, context)) if branch else None
    if edit is not None:
        await query.edit_message_text(
            text=edit.text, reply_markup=edit.reply_markup, parse_mode=edit.parse_mode
        )


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
            logger.warning("tapped Confirm with no SETUP game left — ignoring")
            return None
        # DEBUG, not INFO: activate_game logs the start itself, naming the
        # starter and the answer, and this tap is that same action.
        logger.debug("confirmed from preview", game_id=setup_game.id)
        first_stage_post = _activate_and_stage_first_post(
            session, context, setup_game, lang, user.full_name
        )
    # Block closed and committed above — activation is durable now
    # regardless of whether the announcement below actually reaches the
    # group (see post_current_image's docstring).
    await timeout_module.post_stage_image(
        context, session_factory, photo=first_stage_post.photo, caption=first_stage_post.caption
    )
    return lang


@dataclass(frozen=True)
class _Tap:
    """What a preview branch works from, inside preview_callback_handler's
    session."""

    session: Session
    game: Game
    lang: str
    context: ContextTypes.DEFAULT_TYPE


@dataclass(frozen=True)
class _Edit:
    """The message edit a preview branch answers with, sent only once the
    branch's session has committed."""

    text: str
    reply_markup: InlineKeyboardMarkup | None = None
    parse_mode: str | None = None


def _preview_change_image(tap: _Tap) -> _Edit:
    """A genuine upload (screenshot_source unset) goes straight to
    asking for a new photo, exactly as before ticket 9. An API-sourced
    screenshot instead offers a choice — upload one after all, or pick
    a different screenshot from the same provider — via
    _preview_change_image_upload/_preview_change_image_pick_screenshot."""
    game, lang = tap.game, tap.lang
    logger.info("tapped Change image on the preview", game_id=game.id)
    if game.screenshot_source is not None:
        return _Edit(
            i18n.t("dm_start.pick_new_image_source_prompt", lang), change_image_keyboard(lang)
        )
    game.setup_step = SetupStep.AWAITING_PHOTO_CHANGE
    return _Edit(i18n.t("dm_start.ask_new_photo", lang))


def _preview_change_image_upload(tap: _Tap) -> _Edit:
    logger.info("chose to upload a new photo from the preview", game_id=tap.game.id)
    tap.game.setup_step = SetupStep.AWAITING_PHOTO_CHANGE
    return _Edit(i18n.t("dm_start.ask_new_photo", tap.lang))


async def _handle_change_image_pick_screenshot_tap(
    context: ContextTypes.DEFAULT_TYPE, session_factory, query, user
) -> None:
    """Split out from preview_callback_handler's shared session, and itself
    in phases (issue #156): the picker state commits first, the provider
    fetch and gallery send run after, and a ScreenshotFailure is staged
    (stage_fallback re-arms setup_step/screenshot_picker_provider) in a
    second short session before its notice goes out — so no Telegram call
    or provider round-trip ever holds a transaction open or can roll back
    what the starter is already looking at (see post_current_image's
    docstring)."""
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        setup_game = game_service.get_setup_game_for_starter(session, user.id)
        if setup_game is None:
            logger.warning("tapped Pick a different screenshot with no SETUP game left — ignoring")
            return
        logger.info("chose to pick a different screenshot from the preview", game_id=setup_game.id)
        resume = stage_gallery_resume(setup_game)
        stale_menu = source_menu_for(setup_game, None) if resume is None else None
    if stale_menu is not None:
        # A stale tap: the source this gallery would have resumed is
        # gone, so no gallery (and therefore no buttons) goes out
        # alongside this message. It has to carry the source menu
        # itself, plain — there is no provider at fault to flag — or the
        # starter reads "Where should I get a screenshot from?" with
        # nothing to tap (MECHANICS.md's "When a provider fails").
        logger.warning("stale pick-a-screenshot tap, re-offering the source menu")
        await reply_with_source_menu(
            query.edit_message_text, stale_menu, lang, NO_SOURCE_PROMPT_KEY
        )
        return
    if resume is None:
        raise RuntimeError("stage_gallery_resume returned neither a resume nor a stale menu")
    outcome = await send_gallery_resume(context, resume, lang)
    if not isinstance(outcome, ScreenshotFailure):
        await query.edit_message_text(text=i18n.t(outcome, lang))
        return
    with session_scope(session_factory) as session:
        setup_game = game_service.get_setup_game_for_starter(session, user.id)
        if setup_game is None:
            logger.warning("setup vanished while its gallery was loading — dropping the fallback")
            return
        notice = stage_fallback(setup_game, outcome)
    # Block closed and committed above — see stage_fallback's docstring
    # for why the send has to happen after.
    await send_fallback_notice(query.edit_message_text, notice, lang)


def _preview_research(tap: _Tap) -> _Edit:
    game, lang = tap.game, tap.lang
    logger.info("tapped Re-search title on the preview", game_id=game.id)
    # An API-sourced screenshot is cleared here (see
    # clear_screenshot_selection's docstring) since re-searching might
    # pick a different anime entirely — a genuine upload is left alone,
    # unchanged from before ticket 9.
    clear_screenshot_selection(game)
    game.setup_step = SetupStep.PICKING_METHOD
    return _Edit(
        i18n.t(_method_prompt_key(prefer_shikimori=_prefer_shikimori(lang)), lang),
        _method_keyboard(tap.context, lang),
    )


def _preview_add_synonym(tap: _Tap) -> _Edit:
    logger.info("tapped Add a synonym on the preview", game_id=tap.game.id)
    tap.game.setup_step = SetupStep.AWAITING_SYNONYM
    return _Edit(i18n.t("dm_start.ask_extra_synonym", tap.lang))


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


def _preview_pixel_algorithm(tap: _Tap) -> _Edit:
    game, lang = tap.game, tap.lang
    logger.info("opened the pixelation submenu", game_id=game.id)
    return _Edit(
        i18n.t("dm_start.pixel_algorithm_prompt", lang, options=_algorithm_options(lang)),
        pixel_algorithm_keyboard(lang, game.pixel_algorithm),
        parse_mode="HTML",
    )


def _back_to_preview(tap: _Tap) -> _Edit:
    """The same prompt and buttons the album's follow-up message started with."""
    return _Edit(
        i18n.t("dm_start.preview_confirm_prompt", tap.lang),
        preview_keyboard(tap.lang, tap.game.pixel_algorithm),
    )


def _preview_pixel_algorithm_back(tap: _Tap) -> _Edit:
    """Leave the submenu without changing anything."""
    logger.info("closed the pixelation submenu without changing it", game_id=tap.game.id)
    return _back_to_preview(tap)


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
        logger.warning("ignoring unknown pixelation algorithm {value!r}", value=value)
        return

    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        setup_game = game_service.get_setup_game_for_starter(session, user.id)
        if setup_game is None:
            logger.warning(
                "picked pixelation algorithm {algorithm} with no SETUP game left — ignoring",
                algorithm=algorithm.value,
            )
            return
        setup_game.pixel_algorithm = algorithm
        logger.info(
            "set the pixelation algorithm to {algorithm}",
            algorithm=algorithm.value,
            game_id=setup_game.id,
        )
        album = _stage_preview(session, setup_game, lang)
    # Block closed and committed above — see _post_preview_album's
    # docstring for why the sends have to happen after.
    await query.edit_message_text(
        text=i18n.t("dm_start.pixel_algorithm_updated", lang, name=algorithm_name(algorithm, lang))
    )
    await _post_preview_album(context, album, lang)


def _bounty_menu(session, game: Game, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    """The bounty submenu's text and keyboard: the current pot and the
    setter's balance, with only the presets that balance covers."""
    balance = wallet.balance(session, game.starter_id)
    text = i18n.t(
        "dm_start.bounty_prompt",
        lang,
        pot=bounty.pot_balance(session, game.id),
        balance=balance,
    )
    if balance < min(BOUNTY_PRESETS):
        text += "\n" + i18n.t("dm_start.bounty_none_affordable", lang)
    return text, bounty_keyboard(lang, balance)


def _preview_bounty(tap: _Tap) -> _Edit:
    logger.info("opened the bounty submenu", game_id=tap.game.id)
    text, markup = _bounty_menu(tap.session, tap.game, tap.lang)
    return _Edit(text, markup)


def _preview_bounty_back(tap: _Tap) -> _Edit:
    logger.info("closed the bounty submenu", game_id=tap.game.id)
    return _back_to_preview(tap)


# The preview buttons that only change the setup step and the message. The
# ones that post albums or answer with alerts have handlers of their own.
_BRANCHES: Mapping[str, Callable[[_Tap], _Edit]] = MappingProxyType(
    {
        PREVIEW_CHANGE_IMAGE_CALLBACK_DATA: _preview_change_image,
        PREVIEW_CHANGE_IMAGE_UPLOAD_CALLBACK_DATA: _preview_change_image_upload,
        PREVIEW_RESEARCH_CALLBACK_DATA: _preview_research,
        PREVIEW_ADD_SYNONYM_CALLBACK_DATA: _preview_add_synonym,
        PREVIEW_PIXEL_ALGORITHM_CALLBACK_DATA: _preview_pixel_algorithm,
        PREVIEW_PIXEL_ALGORITHM_BACK_CALLBACK_DATA: _preview_pixel_algorithm_back,
        PREVIEW_BOUNTY_CALLBACK_DATA: _preview_bounty,
        PREVIEW_BOUNTY_BACK_CALLBACK_DATA: _preview_bounty_back,
    }
)


async def _handle_bounty_pick_tap(session_factory, query, user) -> None:
    """Put the tapped preset into the setter's SETUP game's pot and
    re-show the submenu. Owns its session (and the query's one answer):
    a refusal rolls the block back and alerts instead of editing."""
    try:
        amount = int(str(query.data).removeprefix(PREVIEW_BOUNTY_PICK_PREFIX))
    except ValueError:
        amount = None
    if amount not in BOUNTY_PRESETS:
        # Callback data is client-supplied (see keyboards.py's trust-boundary note).
        logger.warning("ignoring bounty amount {data!r}", data=query.data)
        await query.answer()
        return

    refusal: bounty.BountyRefusal | None = None
    shown: tuple[str, InlineKeyboardMarkup] | None = None
    try:
        with session_scope(session_factory) as session:
            lang = settings.get_language(session)
            setup_game = game_service.get_setup_game_for_starter(session, user.id)
            player = session.get(Player, user.id)
            if setup_game is None or player is None:
                await _reject_stale_tap(query, lang)
                return
            bounty.contribute(session, setup_game, player, amount)
            shown = _bounty_menu(session, setup_game, lang)
    except bounty.BountyRefusedError as error:
        refusal = error.refusal
    if refusal is not None:
        await _alert_bounty_refusal(session_factory, query, user, refusal)
        return
    if shown is None:
        raise RuntimeError("bounty pick finished with neither a refusal nor a menu")
    await query.answer()
    await query.edit_message_text(text=shown[0], reply_markup=shown[1])


async def _alert_bounty_refusal(
    session_factory, query, user, refusal: bounty.BountyRefusal
) -> None:
    logger.warning("preview bounty was refused: {reason}", reason=refusal.value)
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        balance = wallet.balance(session, user.id)
    await query.answer(
        i18n.t(f"bounty.refusal.{refusal.value}", lang, minimum=bounty.BOUNTY_MIN, balance=balance),
        show_alert=True,
    )
