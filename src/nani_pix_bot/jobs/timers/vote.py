"""The hard-mode vote's Telegram side (issue #252): the ballot post, its
15-minute close timer (re-armed on startup, which also re-posts a ballot
a restart kept from going out), and the closing tail. A win
pays out and hands over the turn; no winner settles the game as unsolved
and opens the turn. Either way the reveal cleanup follows. The rules live
in services/game/vote.py."""

from dataclasses import dataclass
from typing import cast

from loguru import logger
from sqlalchemy.orm import Session, sessionmaker
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.error import TelegramError
from telegram.ext import ContextTypes, JobQueue

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs.timers._shared import job_log_scope, seconds_until
from nani_pix_bot.jobs.timers.current_image import (
    RevealTarget,
    clear_image_if_sent,
    post_reveal_pair,
)
from nani_pix_bot.jobs.timers.quiet import quiet_hours_deferred
from nani_pix_bot.jobs.timers.retry import retry_on_failure
from nani_pix_bot.jobs.timers.turn_timers import schedule_turn_timers
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, settings
from nani_pix_bot.services.economy import messages, settlement
from nani_pix_bot.services.game import guesses

VOTE_PREFIX = "vote:"
# Telegram shows roughly this much of a button label on a phone.
_LABEL_LIMIT = 48


def vote_close_job_name(game_id: int) -> str:
    return f"vote-close-{game_id}"


def schedule_vote_close(job_queue: JobQueue | None, game: Game) -> None:
    if job_queue is None:
        return
    cancel_vote_close(job_queue, game.id)
    delay = seconds_until(game.vote_deadline_at)
    logger.debug("scheduling vote close in {delay:.0f}s", delay=delay, game_id=game.id)
    job_queue.run_once(
        vote_close_job_callback, when=delay, name=vote_close_job_name(game.id), data=game.id
    )


def ballot_repost_job_name(game_id: int) -> str:
    return f"vote-ballot-{game_id}"


def rearm_vote(job_queue: JobQueue | None, game: Game) -> None:
    """On startup: re-arm the close timer, and re-post the ballot if a
    restart came between opening the vote and posting it (no
    vote_message_id) while there is still time to vote."""
    schedule_vote_close(job_queue, game)
    if job_queue is None or game.vote_message_id is not None:
        return
    if seconds_until(game.vote_deadline_at) <= 0:
        return
    logger.info("re-posting the ballot for an open vote", game_id=game.id)
    job_queue.run_once(
        ballot_repost_job_callback, when=0, name=ballot_repost_job_name(game.id), data=game.id
    )


def cancel_vote_close(job_queue: JobQueue | None, game_id: int) -> None:
    if job_queue is None:
        return
    for job in job_queue.get_jobs_by_name(vote_close_job_name(game_id)):
        job.schedule_removal()


def _handle(session: Session, player_id: int) -> str:
    player = session.get(Player, player_id)
    if player is not None and player.username:
        return f"@{player.username}"
    return str(player_id)


def ballot_text(session: Session, game: Game, lang: str) -> str:
    total = sum(game_service.vote_counts(session, game.id).values())
    minutes = int(game_service.VOTE_DURATION.total_seconds() // 60)
    text = i18n.t(
        "vote.ballot", lang, minutes=minutes, min_votes=game_service.VOTE_MIN_VOTES, total=total
    )
    return text + game_service.game_id_line(game.id, lang)


def _label(session: Session, player_id: int, texts: list[str], votes: int) -> str:
    label = f"{_handle(session, player_id)}: {' / '.join(texts)}"
    if len(label) > _LABEL_LIMIT:
        label = label[: _LABEL_LIMIT - 1] + "…"
    return f"{label} — {votes}"


def ballot_markup(session: Session, game: Game) -> InlineKeyboardMarkup:
    counts = game_service.vote_counts(session, game.id)
    rows = [
        [
            InlineKeyboardButton(
                _label(session, player_id, texts, counts.get(player_id, 0)),
                callback_data=f"{VOTE_PREFIX}{game.id}:{player_id}",
            )
        ]
        for player_id, texts in guesses.guess_texts(session, game.id).items()
    ]
    return InlineKeyboardMarkup(rows)


async def post_vote_ballot(
    context: ContextTypes.DEFAULT_TYPE, session_factory: sessionmaker[Session], game_id: int
) -> None:
    """Post the answer and both screenshots (kept, not cleared, while the vote
    runs), then the ballot as its own message — an album can't carry
    buttons. The caller already scheduled the close timer."""
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        game = session.get(Game, game_id)
        if game is None or game.status != GameStatus.VOTING:
            logger.warning("asked to post a ballot for a game that isn't VOTING", game_id=game_id)
            return
        photos = game_service.hard_mode_reveal_images(game)
        title = game_service.display_title(game, lang)
        caption = i18n.t("vote.reveal_caption", lang, title=title)
        caption += game_service.game_id_line(game_id, lang)
        text, markup = ballot_text(session, game, lang), ballot_markup(session, game)
    await post_reveal_pair(
        context,
        session_factory,
        target=RevealTarget(game_id, None),
        photos=photos,
        caption=caption,
    )
    try:
        message = await context.bot.send_message(
            chat_id=context.bot_data["group_chat_id"],
            message_thread_id=context.bot_data["game_topic_id"],
            text=text,
            reply_markup=markup,
        )
    except TelegramError as exc:
        # The timer still closes the vote; nobody can vote, so it ends unsolved.
        logger.error("failed to post the vote ballot: {error}", error=exc, game_id=game_id)
        return
    with session_scope(session_factory) as session:
        game = session.get(Game, game_id)
        if game is not None:
            game.vote_message_id = message.message_id
    logger.info("vote ballot posted (msg {msg_id})", msg_id=message.message_id, game_id=game_id)


@dataclass(frozen=True)
class VoteFinal:
    winner_id: int | None
    winner_name: str | None


def _settle(session: Session, context, game: Game, lang: str, forced_winner_id: int | None):
    """The in-transaction half of finalize_vote: decide, pay, word the post."""
    title = game_service.display_title(game, lang)
    winner_id = game_service.close_vote(session, game, forced_winner_id=forced_winner_id)
    if winner_id is None:
        text = i18n.t("vote.no_winner", lang, title=title)
        text += settlement.settle_unsolved(session, game, lang)
        game_service.mark_turn_open_if_unassigned(session)
        return VoteFinal(None, None), text
    winner_name = _handle(session, winner_id)
    earnings = settlement.settle_disputed_win(session, game, winner_id)
    key = "vote.won" if forced_winner_id is None else "vote.admin_won"
    text = i18n.t(key, lang, winner=winner_name, title=title)
    text += messages.earnings_suffix(session, game, earnings, lang, player_name=winner_name)
    turn_state = game_service.get_turn_state(session)
    if turn_state is not None:
        schedule_turn_timers(context.job_queue, turn_state)
    return VoteFinal(winner_id, winner_name), text


async def finalize_vote(
    context: ContextTypes.DEFAULT_TYPE,
    session_factory: sessionmaker[Session],
    game_id: int,
    *,
    forced_winner_id: int | None = None,
) -> VoteFinal | None:
    """Close a VOTING game (from the timer, or an admin's /setwinner) and
    post the result. Returns None, doing nothing, if the game is no longer
    VOTING — a late timer after /setwinner or /stop. The caller rolls for
    an overthrow afterwards."""
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        game = session.get(Game, game_id)
        if game is None or game.status != GameStatus.VOTING:
            logger.debug("vote close for a game that isn't VOTING — no-op", game_id=game_id)
            return None
        ballot_id = game.vote_message_id
        final, text = _settle(session, context, game, lang, forced_winner_id)
        text += game_service.game_id_line(game_id, lang)
    cancel_vote_close(context.job_queue, game_id)
    await _close_ballot(context, ballot_id)
    sent = await _send_topic_text(context, text)
    clear_image_if_sent(session_factory, game_id, sent)
    return final


async def _close_ballot(context: ContextTypes.DEFAULT_TYPE, message_id: int | None) -> None:
    if message_id is None:
        return
    try:
        await context.bot.edit_message_reply_markup(
            chat_id=context.bot_data["group_chat_id"], message_id=message_id, reply_markup=None
        )
    except TelegramError as exc:
        logger.warning("couldn't remove the ballot buttons: {error}", error=exc)


async def _send_topic_text(context: ContextTypes.DEFAULT_TYPE, text: str) -> Message | None:
    try:
        return await context.bot.send_message(
            chat_id=context.bot_data["group_chat_id"],
            message_thread_id=context.bot_data["game_topic_id"],
            text=text,
        )
    except TelegramError as exc:
        logger.error("failed to post the vote result: {error}", error=exc)
        return None


@job_log_scope("game_id")
@retry_on_failure
@quiet_hours_deferred
async def ballot_repost_job_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled by rearm_vote: post the ballot a restart kept from going out."""
    job = context.job
    if job is None:
        return
    await post_vote_ballot(context, context.bot_data["session_factory"], cast(int, job.data))


@job_log_scope("game_id")
@retry_on_failure
@quiet_hours_deferred
async def vote_close_job_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fires VOTE_DURATION after a vote opened. Imports maybe_overthrow
    lazily, for the same reason game_timeout.py's timeout_job_callback does."""
    from nani_pix_bot.jobs.timers.autostart import maybe_overthrow

    job = context.job
    if job is None:
        return
    session_factory = context.bot_data["session_factory"]
    final = await finalize_vote(context, session_factory, cast(int, job.data))
    if final is None:
        return
    await maybe_overthrow(
        context, session_factory, winner_id=final.winner_id, winner_name=final.winner_name
    )
