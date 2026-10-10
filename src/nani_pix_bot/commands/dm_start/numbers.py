"""The answer to "do the numbers in this title matter?" (issue #345),
asked right before the preview for a number-heavy title — see
MECHANICS.md's "Starting a game" and _shared.py's _stage_preview."""

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start._shared import (
    _post_preview_album,
    _reject_stale_tap,
    _stage_preview,
)
from nani_pix_bot.commands.dm_start.keyboards import (
    NUMBERS_NO_CALLBACK_DATA,
    NUMBERS_YES_CALLBACK_DATA,
)
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import SetupStep
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, settings

_ANSWERS = {NUMBERS_YES_CALLBACK_DATA: True, NUMBERS_NO_CALLBACK_DATA: False}


async def numbers_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Store the answer, then show the preview it was holding back. The
    question message keeps the answer as its text and loses its buttons,
    so the album's own keyboard is the only live one."""
    query = update.callback_query
    if query is None or query.from_user is None:
        return
    numbers_matter = _ANSWERS.get(str(query.data))
    if numbers_matter is None:
        # Callback data is client-supplied (see keyboards.py's trust-boundary note).
        logger.warning("ignoring unknown numbers answer {data!r}", data=query.data)
        await query.answer()
        return
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        setup_game = game_service.get_setup_game_for_starter(session, query.from_user.id)
        if setup_game is None or setup_game.setup_step != SetupStep.ASKING_NUMBERS:
            logger.warning(
                "answered the numbers question ({answer}) with no game asking it — ignoring",
                answer="yes" if numbers_matter else "no",
            )
            await _reject_stale_tap(query, lang)
            return
        setup_game.numbers_matter = numbers_matter
        logger.info(
            "answered that the numbers {verdict}",
            verdict="matter" if numbers_matter else "don't matter",
            numbers_matter=numbers_matter,
            game_id=setup_game.id,
        )
        album = _stage_preview(session, setup_game, lang)
    # Block closed and committed above — see _post_preview_album's
    # docstring for why the sends have to happen after.
    await query.answer()
    answered = "yes" if numbers_matter else "no"
    await query.edit_message_text(text=i18n.t(f"dm_start.numbers_answered_{answered}", lang))
    await _post_preview_album(context, album, lang)
