"""The win-turn 15min-reminder/12h-expiry timers — see MECHANICS.md's
"Turn handoff" section."""

from loguru import logger
from telegram.error import Forbidden
from telegram.ext import ContextTypes, JobQueue

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs.timers._shared import seconds_until
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.turn_state import TurnState
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, settings

# Singleton names — there's never more than one "pending turn" at a
# time, unlike the per-game timeout/setup-abandon jobs.
TURN_REMINDER_JOB_NAME = "turn-reminder"
TURN_EXPIRY_JOB_NAME = "turn-expiry"


def schedule_turn_timers(job_queue: JobQueue | None, turn_state: TurnState) -> None:
    """(Re)schedules both singleton jobs from the row's stored
    deadlines — cancels any existing ones first so calling this twice
    (e.g. /skip re-assigning to someone else) never double-schedules.

    Each job carries `data=next_starter_id`: JobQueue scheduling isn't
    transactional (a job, once scheduled, can't be undone by a later DB
    rollback), so without this a win/skip that retargets the turn right
    after an earlier one rolled back could leave a reminder/expiry job
    live for a player who is no longer actually designated. Both
    callbacks re-check `job.data` against the live `turn_state` and
    no-op on a mismatch — the same defense-in-depth pattern the per-game
    jobs (game_timeout.py, inactivity.py) already use via their own
    game-id-specific job names."""
    if job_queue is None:
        return
    cancel_turn_timers(job_queue)
    next_starter_id = turn_state.next_starter_id
    if turn_state.reminder_at is not None:
        delay = seconds_until(turn_state.reminder_at)
        logger.debug("Scheduling turn-reminder for {} in {:.0f}s", next_starter_id, delay)
        job_queue.run_once(
            turn_reminder_job_callback,
            when=delay,
            name=TURN_REMINDER_JOB_NAME,
            data=next_starter_id,
        )
    if turn_state.expiry_at is not None:
        delay = seconds_until(turn_state.expiry_at)
        logger.debug("Scheduling turn-expiry for {} in {:.0f}s", next_starter_id, delay)
        job_queue.run_once(
            turn_expiry_job_callback,
            when=delay,
            name=TURN_EXPIRY_JOB_NAME,
            data=next_starter_id,
        )


def cancel_turn_timers(job_queue: JobQueue | None) -> None:
    if job_queue is None:
        return
    logger.debug("Canceling turn-reminder/expiry timers")
    for name in (TURN_REMINDER_JOB_NAME, TURN_EXPIRY_JOB_NAME):
        for job in job_queue.get_jobs_by_name(name):
            job.schedule_removal()


async def turn_reminder_job_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fires 15min after a turn is designated to a real player. DMs
    them a reminder; falls back to an @mention in the group if the DM
    fails (they've never started the bot), same pattern as onboarding's
    /help fallback."""
    job = context.job
    if job is None:
        return
    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        turn_state = game_service.get_turn_state(session)
        if turn_state is None or turn_state.next_starter_id is None:
            logger.debug("Turn-reminder fired but no turn is designated — no-op")
            return
        if turn_state.next_starter_id != job.data:
            logger.debug(
                "Turn-reminder fired for stale target {} (currently {}) — no-op",
                job.data,
                turn_state.next_starter_id,
            )
            return
        if game_service.active_or_setup_game(session) is not None:
            logger.debug("Turn-reminder fired but a game is already running — no-op")
            return
        target_id = turn_state.next_starter_id
        target = session.get(Player, target_id)
        username = target.username if target is not None else None

    try:
        await context.bot.send_message(chat_id=target_id, text=i18n.t("turn.reminder_dm", lang))
        logger.info("Turn reminder DM sent to {}", target_id)
    except Forbidden:
        logger.warning("Turn reminder DM to {} failed — falling back to group mention", target_id)
        text = (
            i18n.t("turn.reminder_group_fallback", lang, username=username)
            if username
            else i18n.t("turn.reminder_group_fallback_unknown", lang)
        )
        await context.bot.send_message(
            chat_id=context.bot_data["group_chat_id"],
            message_thread_id=context.bot_data["game_topic_id"],
            text=text,
        )


async def turn_expiry_job_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fires 12h after a turn is designated to a real player, if they
    never started. Opens the turn to anyone and notifies the group.

    Imports schedule_idle_autostart lazily (function-local, not at module
    level): autostart.py already imports cancel_turn_timers from this
    module, so a module-level import the other way round would be a real
    circular import, not just the theoretical shape the sibling-import
    convention is meant to dodge — Python's import system can't resolve
    two modules that both need each other fully initialized at parse
    time. setup_abandon.py doesn't have this problem (autostart.py
    doesn't import from it), so its import stays at module level."""
    from nani_pix_bot.jobs.timers.autostart import schedule_idle_autostart

    job = context.job
    if job is None:
        return
    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        turn_state = game_service.get_turn_state(session)
        if turn_state is None or turn_state.next_starter_id is None:
            logger.debug("Turn-expiry fired but no turn is designated — no-op")
            return
        if turn_state.next_starter_id != job.data:
            logger.debug(
                "Turn-expiry fired for stale target {} (currently {}) — no-op",
                job.data,
                turn_state.next_starter_id,
            )
            return
        if game_service.active_or_setup_game(session) is not None:
            logger.debug("Turn-expiry fired but a game is already running — no-op")
            return
        expired_id = turn_state.next_starter_id
        turn_state = game_service.set_next_starter(session, None)

    logger.info("Turn for {} expired after 12h — opening to anyone", expired_id)
    cancel_turn_timers(context.job_queue)
    schedule_idle_autostart(context.job_queue, turn_state)
    await context.bot.send_message(
        chat_id=context.bot_data["group_chat_id"],
        message_thread_id=context.bot_data["game_topic_id"],
        text=i18n.t("turn.expired", lang),
    )
