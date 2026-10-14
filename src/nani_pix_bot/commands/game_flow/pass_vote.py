"""/pass — vote to give up on a screenshot nobody recognizes (#428). See MECHANICS.md's
"Giving up together (/pass)"."""

from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy.orm import Session
from telegram import Update, User
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.scoping import is_game_topic
from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs.timers.game_timeout import Ending, cancel_timeout, end_unsolved
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, settings
from nani_pix_bot.services.cosmetics import flair
from nani_pix_bot.services.game import pass_vote


@dataclass(frozen=True)
class _Outcome:
    """What a /pass did: the reply, and — for a counted vote or withdrawal — the tally and
    the game it was for."""

    reply: str
    tally: pass_vote.PassTally | None = None
    game_id: int = 0


def _ending(tally: pass_vote.PassTally) -> Ending:
    return Ending(
        cause=f"pass vote ({tally.votes}/{tally.needed})",
        caption_key="pass.caption",
        hard_mode_caption_key="pass.hard_mode_caption",
    )


def _cast(session: Session, user: User, bot_id: int, lang: str) -> _Outcome:
    game = game_service.active_or_setup_game(session)
    if game is None or game.status != GameStatus.ACTIVE:
        logger.warning("refused /pass: no active round")
        return _Outcome(i18n.t("pass.no_game", lang))
    active = pass_vote.recent_active_count(
        session, datetime.now(UTC), exclude={game.starter_id, bot_id}
    )
    try:
        tally = pass_vote.toggle(session, game, user.id, active=active)
    except pass_vote.PassRefusedError:
        logger.warning("refused /pass from the round's own host")
        return _Outcome(i18n.t("pass.host", lang))
    key = "pass.voted" if tally.voted else "pass.withdrawn"
    name = flair.named(session, user.id, user.full_name)
    reply = i18n.t(key, lang, name=name, votes=tally.votes, needed=tally.needed)
    return _Outcome(reply, tally, game.id)


async def pass_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user = update.message, update.effective_user
    if message is None or user is None:
        return
    group_chat_id = context.bot_data["group_chat_id"]
    game_topic_id = context.bot_data["game_topic_id"]
    if not is_game_topic(update, group_chat_id=group_chat_id, game_topic_id=game_topic_id):
        logger.warning("/pass outside the game topic — ignored")
        return
    with session_scope(context.bot_data["session_factory"]) as session:
        outcome = _cast(session, user, context.bot.id, settings.get_language(session))
    tally = outcome.tally
    if tally is not None:
        logger.info(
            "{action} to give up — {votes}/{needed} ({active} active in 24h)",
            action="voted" if tally.voted else "withdrew their vote",
            votes=tally.votes,
            needed=tally.needed,
            active=tally.active,
            game_id=outcome.game_id,
        )
    try:
        await message.reply_text(outcome.reply)
    except TelegramError as exc:
        logger.warning("failed to send the /pass reply: {error}", error=exc)
    # Only a cast vote gives up: the bar can drop as the 24h window moves,
    # but taking a vote back never ends the round.
    if tally is None or not (tally.voted and tally.reached):
        return
    if await end_unsolved(context, outcome.game_id, _ending(tally)):
        # After the ending, not before: a failed one keeps its 2-day backstop.
        cancel_timeout(context.job_queue, outcome.game_id)
        return
    logger.warning(
        "the give-up vote reached {votes}/{needed} but ended nothing — the round was already over",
        votes=tally.votes,
        needed=tally.needed,
        game_id=outcome.game_id,
    )
