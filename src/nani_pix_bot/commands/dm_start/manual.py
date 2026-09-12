"""Manual title/synonym entry — for anime neither AniList nor Shikimori
knows about. Two DM text messages: the title, then at least one
synonym."""

from loguru import logger
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start._shared import _SYNONYM_SPLIT_RE
from nani_pix_bot.commands.dm_start.preview import _show_preview
from nani_pix_bot.db import session_scope
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n


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
        logger.debug("Game {}: manual title set to {!r}", setup_game.id, title)

    await message.reply_text(i18n.t("dm_start.ask_synonyms", lang))


async def _manual_synonyms_step(
    message, context: ContextTypes.DEFAULT_TYPE, lang: str, user
) -> None:
    """The second manual-entry text message: at least one synonym. On
    success, stages the entry and shows the confirmation preview — same
    as an AniList/Shikimori pick."""
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
        logger.debug("Game {}: manual entry staged with {} synonyms", setup_game.id, len(synonyms))
        await _show_preview(context, session, setup_game, lang)

    await message.reply_text(i18n.t("dm_start.preview_sent", lang))
