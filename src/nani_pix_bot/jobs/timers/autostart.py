"""Bot-initiated game starting: the 24h idle-autostart timer, the
probabilistic "overthrow" trigger fired right after a game concludes —
see the design behind issue #159 — and the season's Gashadokuro boss game
once the 100 Candles goal is reached (#471). Builds on
services/game/autostart.py's pure picking logic; this module owns the
DB write, JobQueue scheduling, and Telegram posting around it (the claim
itself is bot_claim.py's, the boss start boss.py's)."""

from datetime import UTC, datetime

from loguru import logger
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from telegram.ext import ContextTypes, JobQueue

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs.timers._shared import job_log_scope, seconds_until
from nani_pix_bot.jobs.timers.boss import maybe_start_boss, schedule_boss_start
from nani_pix_bot.jobs.timers.bot_claim import (
    IDLE_AUTOSTART_JOB_NAME,
    AutostartClaim,
    AutostartTrigger,
    cancel_idle_autostart,
    describe_holder,
    run_bot_autostart,
)
from nani_pix_bot.jobs.timers.quiet import quiet_hours_deferred
from nani_pix_bot.jobs.timers.retry import retry_on_failure
from nani_pix_bot.models.enums import EventType
from nani_pix_bot.models.turn_state import TurnState
from nani_pix_bot.services import events, quiet_hours, settings
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services.game import autostart as autostart_service


def schedule_idle_autostart(
    job_queue: JobQueue | None, turn_state: TurnState, *, check_boss: bool = True
) -> None:
    """Arms the 24h idle-autostart backstop when the turn is open — and,
    unless `check_boss` is off, a boss check right away: an open turn with
    no game running is when a due Gashadokuro starts (a /stop'd boss, a
    bare /skip, an expired turn). The check job itself decides whether a
    boss is due, so this stays session-free for its many callers."""
    if job_queue is None:
        return
    cancel_idle_autostart(job_queue)
    if turn_state.autostart_deadline_at is None:
        return
    delay = seconds_until(turn_state.autostart_deadline_at)
    logger.debug("scheduling idle-autostart in {delay:.0f}s", delay=delay)
    job_queue.run_once(idle_autostart_job_callback, when=delay, name=IDLE_AUTOSTART_JOB_NAME)
    if check_boss:
        schedule_boss_start(job_queue)


def _autostart_gate_reason(session: Session) -> str | None:
    """Why the bot must not start a game itself right now, or None if
    nothing stops it — the reason goes into the caller's log line."""
    if not settings.get_games_enabled(session):
        return "new games are disabled"
    if not settings.get_autostart_enabled(session):
        return "autostart is disabled"
    if game_service.active_or_setup_game(session) is not None:
        return "a game is already running"
    return None


@job_log_scope()
@retry_on_failure
@quiet_hours_deferred
async def idle_autostart_job_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fires IDLE_AUTOSTART_DELAY after the turn was last opened with no
    game started since. Re-checks everything fresh before attempting a
    claim — a job firing after a human already started a game (or the
    turn was reassigned) must no-op rather than double-post. On a failed
    pick, reschedules AUTOSTART_RETRY_DELAY later rather than going
    silent until the next natural turn-open event. A due Gashadokuro
    boss goes first: this backstop never starts a HARD MODE game ahead of
    it (e.g. after games were switched back on)."""
    session_factory = context.bot_data["session_factory"]
    if await maybe_start_boss(context, session_factory):
        return
    with session_scope(session_factory) as session:
        turn_state = game_service.get_turn_state(session)
        if turn_state is None or turn_state.next_starter_id is not None:
            logger.info("idle-autostart fired but the turn is no longer open — not starting")
            return
        gate_reason = _autostart_gate_reason(session)
        if gate_reason is not None:
            logger.info("idle-autostart fired but {reason} — not starting", reason=gate_reason)
            return

    claim = AutostartClaim(trigger=AutostartTrigger.IDLE, dethroned_winner_name=None)
    started = await run_bot_autostart(context, session_factory, claim)
    if started:
        return

    with session_scope(session_factory) as session:
        turn_state = game_service.get_turn_state(session)
        if turn_state is None or turn_state.next_starter_id is not None:
            return
        turn_state.autostart_deadline_at = game_service.deadline_after(
            session, game_service.AUTOSTART_RETRY_DELAY
        )
        logger.info(
            "idle-autostart pick failed — retrying in {delay}",
            delay=game_service.AUTOSTART_RETRY_DELAY,
        )
        schedule_idle_autostart(context.job_queue, turn_state, check_boss=False)


async def maybe_overthrow(
    context: ContextTypes.DEFAULT_TYPE,
    session_factory,
    *,
    winner_id: int | None = None,
    winner_name: str | None = None,
) -> None:
    """Called right after a game concludes — a win (`winner_id` set) or
    an unsolved/timeout ending (`winner_id` None) — from every one of
    this feature's game-ending call sites. A due Gashadokuro boss comes
    first (maybe_start_boss); when it starts or is scheduled, there is no
    overthrow roll. Otherwise rolls OVERTHROW_PROBABILITY once; on a hit,
    attempts to claim the next game itself, dethroning `winner_id` if one
    was given (they keep their win credit regardless — only the next-turn
    privilege moves). On a miss, a disabled gate, or a failed pick, falls
    through to (re)arming the 24h idle-autostart backstop from whatever
    the normal outcome already committed."""
    boss = await maybe_start_boss(
        context, session_factory, winner_id=winner_id, winner_name=winner_name
    )
    if not boss and await _try_overthrow(context, session_factory, winner_id, winner_name):
        return

    with session_scope(session_factory) as session:
        turn_state = game_service.get_turn_state(session)
        if turn_state is not None:
            # The boss was just tried: only the backstop is left to arm.
            schedule_idle_autostart(context.job_queue, turn_state, check_boss=False)


async def _try_overthrow(
    context: ContextTypes.DEFAULT_TYPE,
    session_factory,
    winner_id: int | None,
    winner_name: str | None,
) -> bool:
    """The overthrow roll and, on a hit, the claim. True when it claimed."""
    if not _roll_overthrow(session_factory, winner_id):
        return False
    claim = AutostartClaim(
        trigger=AutostartTrigger.OVERTHROW,
        dethroned_winner_name=winner_name,
        expected_next_starter_id=winner_id,
    )
    if not await run_bot_autostart(context, session_factory, claim):
        return False
    if winner_id is not None:
        _record_overthrow(session_factory, winner_id)
    return True


def _record_overthrow(session_factory, winner_id: int) -> None:
    """The dethroned winner's `overthrown` event, in its own transaction."""
    try:
        with session_scope(session_factory) as session:
            events.emit(session, EventType.OVERTHROWN, events.Involved(actor_id=winner_id))
    except SQLAlchemyError as error:
        # The game is already claimed; a lost event must not abort the flow.
        logger.error(
            "couldn't record the overthrow of {player}: {error}",
            player=winner_id,
            error=error,
        )


def _roll_overthrow(session_factory, winner_id: int | None) -> bool:
    """maybe_overthrow's gate checks plus the roll itself, logging the
    outcome either way — a skipped or missed roll is as much a part of
    the game's story at INFO as a hit."""
    with session_scope(session_factory) as session:
        gate_reason = _autostart_gate_reason(session)
        if gate_reason is None and quiet_hours.is_quiet(
            settings.get_quiet_hours(session), datetime.now(UTC)
        ):
            gate_reason = "quiet hours"
        holder = describe_holder(session, winner_id)
    if gate_reason is not None:
        logger.info("overthrow roll skipped after a game ended — {reason}", reason=gate_reason)
        return False
    hit = autostart_service.roll_overthrow()
    logger.info(
        "overthrow roll after a game ended: {outcome} ({chance:.0%} chance; turn held by {holder})",
        outcome="HIT — the bot will try to start the next game" if hit else "miss",
        chance=autostart_service.OVERTHROW_PROBABILITY,
        holder=holder,
    )
    return hit
