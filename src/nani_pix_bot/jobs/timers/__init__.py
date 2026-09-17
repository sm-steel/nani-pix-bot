"""Game/turn lifecycle JobQueue wiring: the 2-day game timeout
(`game_timeout.py`), the 1-hour setup-abandon timer (`setup_abandon.py`),
the win-turn 15min-reminder/12h-expiry timers (`turn_timers.py`), the
3h-nudge/6h-auto-advance inactivity timers (`inactivity.py`), and the
shared "post the current image, best-effort pin it" helper
(`current_image.py`) every terminal/stage-advance outcome uses to
announce itself — see MECHANICS.md's "Timeout" and "Turn handoff"
sections. `_shared.py` holds the pure scheduling math (`seconds_until`)
every submodule needs.

Split from a single module (2026-09) once it grew past qlty's
file-total-complexity threshold — the fix for the pre-commit Telegram-
rollback bug (see the milestone this shipped under) added a genuinely
new conditional to two of its callbacks. This `__init__` re-exports
every public name so existing callers (`from nani_pix_bot.jobs import
timers as timeout_module`, or a direct `from nani_pix_bot.jobs.timers
import rearm_pending_timeouts`) needed no changes.

JobQueue jobs don't survive a process restart, so `rearm_pending_timeouts`
is called from app.py on startup to re-schedule every one of these from
its stored absolute deadline (`Game.scheduled_end_at`/`setup_deadline`,
`TurnState.reminder_at`/`expiry_at`) — a redeploy never silently loses or
resets any of these clocks. It lives here rather than in any one
submodule since it touches all of them.
"""

from loguru import logger
from telegram.ext import JobQueue

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs.timers._shared import seconds_until, seconds_until_timeout
from nani_pix_bot.jobs.timers.autostart import (
    IDLE_AUTOSTART_JOB_NAME,
    AutostartTrigger,
    cancel_idle_autostart,
    idle_autostart_job_callback,
    maybe_overthrow,
    run_bot_autostart,
    schedule_idle_autostart,
)
from nani_pix_bot.jobs.timers.current_image import clear_image_if_sent, post_current_image
from nani_pix_bot.jobs.timers.game_timeout import (
    cancel_timeout,
    schedule_timeout,
    timeout_job_callback,
    timeout_job_name,
)
from nani_pix_bot.jobs.timers.inactivity import (
    cancel_inactivity_timers,
    inactivity_advance_job_callback,
    inactivity_advance_job_name,
    inactivity_nudge_job_callback,
    inactivity_nudge_job_name,
    schedule_inactivity_timers,
)
from nani_pix_bot.jobs.timers.setup_abandon import (
    cancel_setup_abandon,
    schedule_setup_abandon,
    setup_abandon_job_callback,
    setup_abandon_job_name,
)
from nani_pix_bot.jobs.timers.turn_timers import (
    TURN_EXPIRY_JOB_NAME,
    TURN_REMINDER_JOB_NAME,
    cancel_turn_timers,
    schedule_turn_timers,
    turn_expiry_job_callback,
    turn_reminder_job_callback,
)
from nani_pix_bot.services import game as game_service

__all__ = [
    "IDLE_AUTOSTART_JOB_NAME",
    "TURN_EXPIRY_JOB_NAME",
    "TURN_REMINDER_JOB_NAME",
    "AutostartTrigger",
    "cancel_idle_autostart",
    "cancel_inactivity_timers",
    "cancel_setup_abandon",
    "cancel_timeout",
    "cancel_turn_timers",
    "clear_image_if_sent",
    "idle_autostart_job_callback",
    "inactivity_advance_job_callback",
    "inactivity_advance_job_name",
    "inactivity_nudge_job_callback",
    "inactivity_nudge_job_name",
    "maybe_overthrow",
    "post_current_image",
    "rearm_pending_timeouts",
    "run_bot_autostart",
    "schedule_idle_autostart",
    "schedule_inactivity_timers",
    "schedule_setup_abandon",
    "schedule_timeout",
    "schedule_turn_timers",
    "seconds_until",
    "seconds_until_timeout",
    "setup_abandon_job_callback",
    "setup_abandon_job_name",
    "timeout_job_callback",
    "timeout_job_name",
    "turn_expiry_job_callback",
    "turn_reminder_job_callback",
]


async def rearm_pending_timeouts(job_queue: JobQueue | None, session_factory) -> None:
    with session_scope(session_factory) as session:
        active = game_service.active_games(session)
        for game in active:
            schedule_timeout(job_queue, game)
            schedule_inactivity_timers(job_queue, game)
        setups = game_service.setup_games(session)
        for game in setups:
            schedule_setup_abandon(job_queue, game)
        turn_state = game_service.get_turn_state(session)
        if turn_state is not None:
            schedule_turn_timers(job_queue, turn_state)
            schedule_idle_autostart(job_queue, turn_state)
    logger.info(
        "Re-armed {} game timeout(s), {} setup-abandon timer(s) on startup",
        len(active),
        len(setups),
    )
