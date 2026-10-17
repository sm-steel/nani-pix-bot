"""Vote buttons on a hard-mode ballot (issue #252), Nobody included (#458)."""

from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger
from telegram import InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import timers as timeout_module
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, players, settings


def _candidate(part: str) -> int | None:
    """A button's candidate: a guesser's id, or Nobody."""
    if part == timeout_module.NOBODY_CALLBACK:
        return game_service.NOBODY
    return int(part)


@dataclass(frozen=True)
class _Tap:
    lang: str
    refusal: game_service.VoteRefusal | None
    close_now: bool = False
    text: str = ""
    markup: InlineKeyboardMarkup | None = None


def _cast(session, game_id: int, voter, candidate_id: int | None) -> _Tap:
    lang = settings.get_language(session)
    players.get_or_create_player(session, voter.id, username=voter.username)
    game = session.get(Game, game_id)
    if game is None:
        return _Tap(lang, game_service.VoteRefusal.CLOSED)
    refusal = game_service.cast_vote(session, game, voter_id=voter.id, candidate_id=candidate_id)
    if refusal is not None:
        return _Tap(lang, refusal)
    if game_service.should_close_early(session, game, datetime.now(UTC)):
        logger.info("a clear lead reached the bar — closing the vote early", game_id=game_id)
        return _Tap(lang, None, close_now=True)
    text = timeout_module.ballot_text(session, game, lang)
    return _Tap(lang, None, text=text, markup=timeout_module.ballot_markup(session, game, lang))


async def vote_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or query.from_user is None:
        return
    _, game_part, candidate_part = query.data.split(":")
    game_id = int(game_part)
    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        tap = _cast(session, game_id, query.from_user, _candidate(candidate_part))
    if tap.refusal is not None:
        await query.answer(i18n.t(f"vote.refused.{tap.refusal.value}", tap.lang))
        return
    await query.answer(i18n.t("vote.counted", tap.lang))
    if tap.close_now:
        # The close removes the ballot's buttons and posts the result.
        await timeout_module.close_vote_now(context, session_factory, game_id)
        return
    try:
        await query.edit_message_text(tap.text, reply_markup=tap.markup)
    except TelegramError as exc:
        # e.g. "message is not modified" when someone re-taps the same vote.
        logger.debug("ballot not re-rendered: {error}", error=exc)
