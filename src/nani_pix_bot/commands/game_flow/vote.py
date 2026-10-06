"""Vote buttons on a hard-mode ballot (issue #252)."""

from loguru import logger
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import timers as timeout_module
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, players, settings


async def vote_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or query.from_user is None:
        return
    voter = query.from_user
    _, game_part, candidate_part = query.data.split(":")
    session_factory = context.bot_data["session_factory"]
    text = ""
    markup = None
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        players.get_or_create_player(session, voter.id, username=voter.username)
        game = session.get(Game, int(game_part))
        if game is None:
            refusal = game_service.VoteRefusal.CLOSED
        else:
            refusal = game_service.cast_vote(
                session, game, voter_id=voter.id, candidate_id=int(candidate_part)
            )
        if refusal is None and game is not None:
            text = timeout_module.ballot_text(session, game, lang)
            markup = timeout_module.ballot_markup(session, game)
    if refusal is not None:
        await query.answer(i18n.t(f"vote.refused.{refusal.value}", lang))
        return
    await query.answer(i18n.t("vote.counted", lang))
    try:
        await query.edit_message_text(text, reply_markup=markup)
    except TelegramError as exc:
        # e.g. "message is not modified" when someone re-taps the same vote.
        logger.debug("ballot not re-rendered: {error}", error=exc)
