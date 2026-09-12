"""DM photo intake — the entry point of the setup flow: a screenshot
starts a new game (or, mid-setup, replaces the staged one after
"Change image" was tapped)."""

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start._shared import _method_prompt_key, _prefer_shikimori
from nani_pix_bot.commands.dm_start.keyboards import method_selection_keyboard
from nani_pix_bot.commands.dm_start.preview import _show_preview
from nani_pix_bot.commands.helpers.membership import is_group_member
from nani_pix_bot.commands.helpers.scoping import is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import timers as timeout_module
from nani_pix_bot.models.enums import SetupStep
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, players, settings


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A DM photo starts game setup, if it's this player's turn."""
    message = update.message
    if not is_private_chat(update) or message is None or not message.photo:
        return
    user = update.effective_user
    if user is None:
        return

    session_factory = context.bot_data["session_factory"]
    file_id = message.photo[-1].file_id

    if await _replace_staged_photo_if_pending(context, session_factory, user, file_id):
        return

    await _start_new_game(message, context, session_factory, user, file_id)


async def _replace_staged_photo_if_pending(
    context: ContextTypes.DEFAULT_TYPE, session_factory, user, file_id: str
) -> bool:
    """A replacement photo for the preview's "Change image" button — not a
    new game, keeps the staged title/synonyms. Returns whether this was
    such a replacement (so photo_handler knows not to treat it as a new
    game's first photo)."""
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        existing = game_service.get_setup_game_for_starter(session, user.id)
        if existing is None or existing.setup_step != SetupStep.AWAITING_PHOTO_CHANGE:
            return False
        logger.debug("Starter {} sent a replacement photo for game {}", user.id, existing.id)
        existing.original_file_id = file_id
        await _show_preview(context, session, existing, lang)
        return True


async def _start_new_game(
    message, context: ContextTypes.DEFAULT_TYPE, session_factory, user, file_id: str
) -> None:
    group_chat_id = context.bot_data["group_chat_id"]
    if not await is_group_member(context.bot, group_chat_id, user.id):
        with session_scope(session_factory) as session:
            lang = settings.get_language(session)
        logger.warning("Non-member {} tried to start a game via DM", user.id)
        await message.reply_text(i18n.t("dm_start.not_a_member", lang))
        return

    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        if not settings.get_games_enabled(session):
            logger.warning("{} tried to start a game while games are disabled", user.id)
            await message.reply_text(i18n.t("dm_start.games_disabled", lang))
            return
        players.get_or_create_player(session, user.id, username=user.username)
        if not game_service.can_start(session, user.id):
            logger.warning("{} tried to start a game out of turn", user.id)
            await message.reply_text(i18n.t("dm_start.not_your_turn", lang))
            return
        new_game = game_service.create_setup_game(
            session, starter_id=user.id, original_file_id=file_id
        )
        # They're clearly not missing their turn if they've already
        # started it — the setup-abandon timer takes over from here.
        game_service.clear_turn_timers(session)

    timeout_module.cancel_turn_timers(context.job_queue)
    timeout_module.schedule_setup_abandon(context.job_queue, new_game)
    await context.bot.send_message(
        chat_id=group_chat_id,
        message_thread_id=context.bot_data["game_topic_id"],
        text=i18n.t("dm_start.setup_started_group_notice", lang, starter=user.full_name),
    )

    prefer_shikimori = _prefer_shikimori(lang)
    await message.reply_text(
        i18n.t(_method_prompt_key(prefer_shikimori=prefer_shikimori), lang),
        reply_markup=method_selection_keyboard(prefer_shikimori=prefer_shikimori, lang=lang),
    )
