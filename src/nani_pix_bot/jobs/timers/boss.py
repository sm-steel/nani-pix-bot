"""The season's Gashadokuro boss game (Halloween, #471): once the 100
Candles goal is reached, Nani Pix starts it herself — after a game ends
(autostart.py's maybe_overthrow tries it first) or when the turn opens."""

from datetime import UTC, datetime

from loguru import logger
from sqlalchemy.orm import Session
from telegram.ext import ContextTypes, JobQueue

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs.timers._shared import job_log_scope, seconds_until
from nani_pix_bot.jobs.timers.bot_claim import AutostartClaim, AutostartTrigger, claim_game, gather
from nani_pix_bot.jobs.timers.retry import retry_on_failure
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import quiet_hours, settings
from nani_pix_bot.services.seasons import group_goal

BOSS_START_JOB_NAME = "boss-start"


def schedule_boss_start(job_queue: JobQueue | None, when: datetime | None = None) -> None:
    """Arms the boss check: now, or at `when` (a quiet window's end, a retry)."""
    if job_queue is None:
        return
    for job in job_queue.get_jobs_by_name(BOSS_START_JOB_NAME):
        job.schedule_removal()
    delay = 0.0 if when is None else seconds_until(when)
    logger.debug("scheduling the boss check in {delay:.0f}s", delay=delay)
    job_queue.run_once(boss_start_job_callback, when=delay, name=BOSS_START_JOB_NAME)


@job_log_scope()
@retry_on_failure
async def boss_start_job_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    """The boss check armed when a turn opens, a quiet window ends or a
    failed pick retries. No quiet-hours decorator: maybe_start_boss holds
    the boss itself, with its own log line."""
    await maybe_start_boss(context, context.bot_data["session_factory"])


def _boss_hold(session: Session) -> str | None:
    """Why a due boss can't start now (for the caller's log line), or None."""
    if not settings.get_games_enabled(session):
        return "new games are disabled"
    if game_service.active_or_setup_game(session) is not None:
        return "a game is running — the boss waits for it to end"
    return None


def _quiet_end(session: Session) -> datetime | None:
    """The end of the quiet window we're in, or None when it isn't quiet."""
    qh = settings.get_quiet_hours(session)
    now = datetime.now(UTC)
    if qh is None or not quiet_hours.is_quiet(qh, now):
        return None
    return quiet_hours.window_end_after(qh, now)


async def maybe_start_boss(
    context: ContextTypes.DEFAULT_TYPE,
    session_factory,
    *,
    winner_id: int | None = None,
    winner_name: str | None = None,
) -> bool:
    """Starts the season's Gashadokuro boss game when one is due (see
    group_goal.boss_due): Nani Pix takes the next turn the way an
    overthrow does — whoever holds it keeps any win credit — and starts a
    normal 5-stage game from a gated pick. A game's end passes its winner
    (`winner_id`/`winner_name`, as to maybe_overthrow): when the boss takes
    that winner's turn, its caption names them. Ignores `autostart_enabled`
    (a season event, not autostart) but honours `games_enabled`.

    True when it started the boss or scheduled it: held until quiet hours
    end, or retried AUTOSTART_RETRY_DELAY after a failed pick. False when
    no boss is due, games are off, a game is running (its end tries
    again), or the claim was aborted."""
    with session_scope(session_factory) as session:
        if not group_goal.boss_due(session):
            logger.debug("no boss game due")
            return False
        hold = _boss_hold(session)
        quiet_end = _quiet_end(session)
        turn_state = game_service.get_turn_state(session)
        holder_id = turn_state.next_starter_id if turn_state is not None else None
    if hold is not None:
        logger.info("boss game due but {reason} — not starting it now", reason=hold)
        return False
    if quiet_end is not None:
        logger.info(
            "boss game held until quiet hours end at {resume_at}",
            resume_at=quiet_end.isoformat(),
        )
        schedule_boss_start(context.job_queue, quiet_end)
        return True
    gathered = await gather(context, session_factory, AutostartTrigger.BOSS)
    if gathered is None:
        _retry_boss(context, session_factory)
        return True
    # A turn handed to someone while the pick was gathered aborts the claim
    # (claim_game's race check) with nothing re-armed: the boss waits for
    # that player's game to end, or their turn to expire and open.
    dethroned = winner_name if winner_id is not None and winner_id == holder_id else None
    claim = AutostartClaim(AutostartTrigger.BOSS, dethroned, expected_next_starter_id=holder_id)
    return await claim_game(context, session_factory, claim, gathered)


def _retry_boss(context: ContextTypes.DEFAULT_TYPE, session_factory) -> None:
    with session_scope(session_factory) as session:
        retry_at = game_service.deadline_after(session, game_service.AUTOSTART_RETRY_DELAY)
    logger.info(
        "boss game pick failed — retrying in {delay}", delay=game_service.AUTOSTART_RETRY_DELAY
    )
    schedule_boss_start(context.job_queue, retry_at)
