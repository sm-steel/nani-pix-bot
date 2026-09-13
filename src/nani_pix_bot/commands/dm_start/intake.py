"""DM photo intake — the entry point of the setup flow: a screenshot
starts a new game (or, mid-setup, replaces the staged one after
"Change image" was tapped)."""

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start._shared import _show_preview, _start_new_game
from nani_pix_bot.commands.helpers.scoping import is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import SetupStep
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import settings


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A DM photo starts game setup, if it's this player's turn."""
    message = update.message
    if not is_private_chat(update) or message is None or not message.photo:
        return
    user = update.effective_user
    if user is None:
        return

    session_factory = context.bot_data["session_factory"]
    # Downloaded immediately, once, rather than storing the Telegram
    # file_id — see models/game.py's original_image docstring for why
    # (removes any dependency on Telegram continuing to serve this
    # file_id for the life of the game).
    telegram_file = await context.bot.get_file(message.photo[-1].file_id)
    image_bytes = bytes(await telegram_file.download_as_bytearray())

    if await _replace_staged_photo_if_pending(context, session_factory, user, image_bytes):
        return

    await _start_new_game(message, context, session_factory, user, image_bytes)


async def _replace_staged_photo_if_pending(
    context: ContextTypes.DEFAULT_TYPE, session_factory, user, image_bytes: bytes
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
        existing.original_image = image_bytes
        await _show_preview(context, session, existing, lang)
        return True
