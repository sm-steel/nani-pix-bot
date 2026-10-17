"""The "↩ Pick a different anime" button (#429), on every DM setup screen
after the anime is identified: forget it and go back to the methods."""

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start._shared import (
    _method_keyboard,
    _method_prompt_key,
    _prefer_shikimori,
    _reject_stale_tap_answered,
)
from nani_pix_bot.commands.dm_start.screenshots import restart_identification
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import SetupStep
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, settings


async def repick_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ "Pick a different anime" (#429), on the screens after the anime is
    identified — the screenshot source menu and gallery, the alias and
    numbers questions: forget the anime and re-show the methods in place.
    Before this, the way out of a wrong pick was /stop and a new /newgame.
    An old button tapped once the setup is already back at the methods
    only drops that message's buttons, as "different search method" does."""
    query = update.callback_query
    if query is None or query.data is None or query.from_user is None:
        return
    await query.answer()
    user = query.from_user
    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        setup_game = game_service.get_setup_game_for_starter(session, user.id)
        if setup_game is None:
            await _reject_stale_tap_answered(context, user.id, query.data, lang)
            return
        step, game_id = setup_game.setup_step, setup_game.id
        if step != SetupStep.PICKING_METHOD:
            title = game_service.display_title(setup_game, "EN")
            restart_identification(setup_game)
    if step == SetupStep.PICKING_METHOD:
        logger.warning(
            "tapped a stale 'pick a different anime' button — already picking a method",
            game_id=game_id,
        )
        await query.edit_message_reply_markup(reply_markup=None)
        return
    logger.info(
        "went back to pick a different anime instead of {title!r} (from {step})",
        title=title,
        step=step,
        game_id=game_id,
    )
    await query.edit_message_text(
        i18n.t(_method_prompt_key(prefer_shikimori=_prefer_shikimori(lang)), lang),
        reply_markup=_method_keyboard(context, lang),
    )
