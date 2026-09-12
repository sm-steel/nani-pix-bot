"""Game/turn lifecycle JobQueue wiring: the 2-day game timeout, the
1-hour setup-abandon timer, and the win-turn 15min-reminder/12h-expiry
timers — see MECHANICS.md's "Timeout" and "Turn handoff" sections.

JobQueue jobs don't survive a process restart, so `rearm_pending_timeouts`
is called from app.py on startup to re-schedule every one of these from
its stored absolute deadline (`Game.scheduled_end_at`/`setup_deadline`,
`TurnState.reminder_at`/`expiry_at`) — a redeploy never silently loses or
resets any of these clocks.
"""

from datetime import UTC, datetime

from loguru import logger
from telegram.error import Forbidden
from telegram.ext import ContextTypes, JobQueue

from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.turn_state import TurnState
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, settings

# Singleton names — there's never more than one "pending turn" at a
# time, unlike the per-game timeout/setup-abandon jobs.
TURN_REMINDER_JOB_NAME = "turn-reminder"
TURN_EXPIRY_JOB_NAME = "turn-expiry"


def seconds_until(deadline: datetime | None) -> float:
    """Seconds from now until `deadline`, clamped at 0 for an already-
    overdue deadline (or if there's no deadline at all). Normalizes naive
    datetimes (as DATETIME columns round-trip from the DB) to UTC before
    comparing — shared by the game timeout, setup-abandon, and win-turn
    reminder/expiry timers below. Lives here rather than in
    services/game/ since it's a pure scheduling helper, not game-state
    logic, and this module is its only caller."""
    if deadline is None:
        return 0.0
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    return max((deadline - datetime.now(UTC)).total_seconds(), 0.0)


def seconds_until_timeout(game: Game) -> float:
    """Seconds from now until `game.scheduled_end_at` — see seconds_until()."""
    return seconds_until(game.scheduled_end_at)


def timeout_job_name(game_id: int) -> str:
    """Deterministic JobQueue job name for a game's timeout — lets this
    module look up and cancel a pending job (e.g. on a win) or re-arm it
    on startup without storing anything extra on the row."""
    return f"game-timeout-{game_id}"


def setup_abandon_job_name(game_id: int) -> str:
    """Deterministic JobQueue job name for a SETUP game's abandon
    timer — mirrors timeout_job_name()."""
    return f"setup-abandon-{game_id}"


def schedule_timeout(job_queue: JobQueue | None, game: Game) -> None:
    if job_queue is None:
        return
    delay = seconds_until_timeout(game)
    logger.debug("Scheduling 2-day timeout for game {} in {:.0f}s", game.id, delay)
    job_queue.run_once(
        timeout_job_callback,
        when=delay,
        name=timeout_job_name(game.id),
        data=game.id,
    )


def cancel_timeout(job_queue: JobQueue | None, game_id: int) -> None:
    if job_queue is None:
        return
    logger.debug("Canceling 2-day timeout for game {}", game_id)
    for job in job_queue.get_jobs_by_name(timeout_job_name(game_id)):
        job.schedule_removal()


async def timeout_job_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    if job is None:
        return
    game_id = job.data

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        game = session.get(Game, game_id)
        if game is None or game.status != GameStatus.ACTIVE or game.original_file_id is None:
            logger.debug("Timeout fired for game {} but it's already resolved — no-op", game_id)
            return

        logger.info("Game {} timed out after 2 days — ending unsolved", game_id)
        game_service.force_unsolved(game)
        await context.bot.send_photo(
            chat_id=context.bot_data["group_chat_id"],
            message_thread_id=context.bot_data["game_topic_id"],
            photo=game.original_file_id,
            caption=i18n.t("timeout.caption", lang, title=game_service.display_title(game)),
        )
        game_service.clear_original_screenshot(game)


async def rearm_pending_timeouts(job_queue: JobQueue | None, session_factory) -> None:
    with session_scope(session_factory) as session:
        active = game_service.active_games(session)
        for game in active:
            schedule_timeout(job_queue, game)
        setups = game_service.setup_games(session)
        for game in setups:
            schedule_setup_abandon(job_queue, game)
        turn_state = game_service.get_turn_state(session)
        if turn_state is not None:
            schedule_turn_timers(job_queue, turn_state)
    logger.info(
        "Re-armed {} game timeout(s), {} setup-abandon timer(s) on startup",
        len(active),
        len(setups),
    )


def schedule_setup_abandon(job_queue: JobQueue | None, game: Game) -> None:
    if job_queue is None:
        return
    delay = seconds_until(game.setup_deadline)
    logger.debug("Scheduling setup-abandon for game {} in {:.0f}s", game.id, delay)
    job_queue.run_once(
        setup_abandon_job_callback,
        when=delay,
        name=setup_abandon_job_name(game.id),
        data=game.id,
    )


def cancel_setup_abandon(job_queue: JobQueue | None, game_id: int) -> None:
    if job_queue is None:
        return
    logger.debug("Canceling setup-abandon timer for game {}", game_id)
    for job in job_queue.get_jobs_by_name(setup_abandon_job_name(game_id)):
        job.schedule_removal()


async def setup_abandon_job_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fires 1h after a SETUP game is created. If the starter never
    confirmed (still SETUP), deletes the orphaned row and opens the turn
    — structurally forecloses the class of bug issue #11 fixed
    reactively (a SETUP row blocking the whole group indefinitely)."""
    job = context.job
    if job is None:
        return
    game_id = job.data

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        game = session.get(Game, game_id)
        if game is None or game.status != GameStatus.SETUP:
            logger.debug("Setup-abandon fired for game {} but it's already resolved", game_id)
            return
        logger.info("Game {} setup abandoned after 1h — deleting and opening the turn", game_id)
        session.delete(game)
        game_service.set_next_starter(session, None)

    await context.bot.send_message(
        chat_id=context.bot_data["group_chat_id"],
        message_thread_id=context.bot_data["game_topic_id"],
        text=i18n.t("dm_start.setup_abandoned", lang),
    )


def schedule_turn_timers(job_queue: JobQueue | None, turn_state: TurnState) -> None:
    """(Re)schedules both singleton jobs from the row's stored
    deadlines — cancels any existing ones first so calling this twice
    (e.g. /skip re-assigning to someone else) never double-schedules."""
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
        )
    if turn_state.expiry_at is not None:
        delay = seconds_until(turn_state.expiry_at)
        logger.debug("Scheduling turn-expiry for {} in {:.0f}s", next_starter_id, delay)
        job_queue.run_once(
            turn_expiry_job_callback,
            when=delay,
            name=TURN_EXPIRY_JOB_NAME,
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
    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        turn_state = game_service.get_turn_state(session)
        if turn_state is None or turn_state.next_starter_id is None:
            logger.debug("Turn-reminder fired but no turn is designated — no-op")
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
    never started. Opens the turn to anyone and notifies the group."""
    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        turn_state = game_service.get_turn_state(session)
        if turn_state is None or turn_state.next_starter_id is None:
            logger.debug("Turn-expiry fired but no turn is designated — no-op")
            return
        if game_service.active_or_setup_game(session) is not None:
            logger.debug("Turn-expiry fired but a game is already running — no-op")
            return
        expired_id = turn_state.next_starter_id
        game_service.set_next_starter(session, None)

    logger.info("Turn for {} expired after 12h — opening to anyone", expired_id)
    cancel_turn_timers(context.job_queue)
    await context.bot.send_message(
        chat_id=context.bot_data["group_chat_id"],
        message_thread_id=context.bot_data["game_topic_id"],
        text=i18n.t("turn.expired", lang),
    )
