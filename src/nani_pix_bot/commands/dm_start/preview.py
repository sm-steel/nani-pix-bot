"""The confirmation preview shown before a game goes live: an album of
every pixelation stage, a follow-up message with confirm/change-image/
research/add-synonym buttons, and (on confirm) the actual post to the
group topic. See MECHANICS.md's "Starting a game" section."""

from loguru import logger
from telegram import InputMediaPhoto, Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start._shared import (
    _SYNONYM_SPLIT_RE,
    _method_prompt_key,
    _prefer_shikimori,
)
from nani_pix_bot.commands.dm_start.keyboards import (
    PREVIEW_ADD_SYNONYM_CALLBACK_DATA,
    PREVIEW_CHANGE_IMAGE_CALLBACK_DATA,
    PREVIEW_CONFIRM_CALLBACK_DATA,
    PREVIEW_RESEARCH_CALLBACK_DATA,
    method_selection_keyboard,
    preview_keyboard,
)
from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import timers as timeout_module
from nani_pix_bot.models.enums import SetupStep
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, settings
from nani_pix_bot.services import pixelate as pixelate_service
from nani_pix_bot.services.settings import stage_config


async def _finalize_and_post(context, session, game: Game, caption: str) -> None:
    """Shared tail end of every identification method (AniList, Shikimori,
    manual): pixelate the original at the first (blockiest) stage, activate the game, post it to
    the group topic, and schedule the timeout — canceling the setup-abandon
    timer that's been running since the photo was first sent (issue #21)."""
    timeout_module.cancel_setup_abandon(context.job_queue, game.id)
    telegram_file = await context.bot.get_file(game.original_file_id)
    original_bytes = bytes(await telegram_file.download_as_bytearray())
    first_stage = game_service.STAGE_ORDER[0]
    target_width = stage_config.get_stage_config(session)[first_stage].target_width
    pixelated = pixelate_service.pixelate(original_bytes, target_width)
    game_service.activate_game(session, game)
    await context.bot.send_photo(
        chat_id=context.bot_data["group_chat_id"],
        message_thread_id=context.bot_data["game_topic_id"],
        photo=pixelated,
        caption=caption,
    )
    timeout_module.schedule_timeout(context.job_queue, game)


async def _show_preview(context, session, game: Game, lang: str) -> None:
    """Send the starter a private preview of every pixelation stage for
    the staged title/synonyms — one Telegram album (blockiest to
    clearest, see MECHANICS.md) — so they see how the round will
    actually look before committing to it. `sendMediaGroup` has no
    `reply_markup` support, so the confirm/change-image/research/add-
    synonym buttons go on a short separate follow-up text message right
    after the album, not on the album itself. Nothing is posted to the
    group until "Confirm and start" is tapped. Always the game's own
    starter's DM — every caller looked this game up by starter_id in the
    first place, so game.starter_id is the right chat_id."""
    telegram_file = await context.bot.get_file(game.original_file_id)
    original_bytes = bytes(await telegram_file.download_as_bytearray())
    config = stage_config.get_stage_config(session)
    synonyms = ", ".join(game.synonyms or []) or "—"
    caption = i18n.t(
        "dm_start.preview_caption", lang, title=game_service.display_title(game), synonyms=synonyms
    )
    captions = [caption, *([None] * (len(game_service.STAGE_ORDER) - 1))]
    media = [
        InputMediaPhoto(
            media=pixelate_service.pixelate(original_bytes, config[stage].target_width),
            caption=stage_caption,
        )
        for stage, stage_caption in zip(game_service.STAGE_ORDER, captions, strict=True)
    ]
    game.setup_step = SetupStep.CONFIRMING
    logger.debug("Game {}: showing {}-stage confirmation preview album", game.id, len(media))
    await context.bot.send_media_group(chat_id=game.starter_id, media=media)
    await context.bot.send_message(
        chat_id=game.starter_id,
        text=i18n.t("dm_start.preview_confirm_prompt", lang),
        reply_markup=preview_keyboard(lang),
    )


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
        elif query.data == PREVIEW_RESEARCH_CALLBACK_DATA:
            await _preview_research(query, setup_game, lang)
        elif query.data == PREVIEW_ADD_SYNONYM_CALLBACK_DATA:
            await _preview_add_synonym(query, setup_game, lang)


async def _preview_confirm(context, session, game: Game, lang: str, starter_name: str) -> None:
    logger.debug("Game {}: confirmed from preview", game.id)
    caption = i18n.t("dm_start.game_started_caption", lang, starter=starter_name)
    await _finalize_and_post(context, session, game, caption)


async def _preview_change_image(query, game: Game, lang: str) -> None:
    logger.debug("Game {}: change-image requested from preview", game.id)
    game.setup_step = SetupStep.AWAITING_PHOTO_CHANGE
    await query.edit_message_text(text=i18n.t("dm_start.ask_new_photo", lang))


async def _preview_research(query, game: Game, lang: str) -> None:
    logger.debug("Game {}: re-search requested from preview", game.id)
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
