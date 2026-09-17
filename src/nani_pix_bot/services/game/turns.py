"""TurnState bookkeeping — who starts the next game, their win-turn
reminder/expiry timers, and the 24h idle-autostart backstop. A related
but distinct concern from the core Game-row state machine in state.py —
see MECHANICS.md's "Turn handoff" section for the rules this implements."""

from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy.orm import Session

from nani_pix_bot.models.turn_state import TurnState

TURN_STATE_ID = 1

# Absolute from a turn being designated to a real user (a win, or
# /skip @user) — see MECHANICS.md's "Turn handoff" section.
TURN_REMINDER_DELAY = timedelta(minutes=15)
TURN_EXPIRY_DELAY = timedelta(hours=12)

# Absolute from the turn becoming open to anyone with no game running —
# see jobs/timers/autostart.py's schedule_idle_autostart(). Retry delay
# is how soon a failed pick attempt (providers down, no screenshots
# found) tries again, rather than going silent until the next natural
# turn-open event.
IDLE_AUTOSTART_DELAY = timedelta(hours=24)
AUTOSTART_RETRY_DELAY = timedelta(hours=1)


def get_turn_state(session: Session) -> TurnState | None:
    return session.get(TurnState, TURN_STATE_ID)


def get_or_create_turn_state(session: Session) -> TurnState:
    """Package-internal: used by state.py's activate_game() to touch the
    turn-state row directly, without set_next_starter()'s reminder/expiry
    side effects. Not part of the public game_service API — most callers
    want set_next_starter() instead."""
    turn_state = get_turn_state(session)
    if turn_state is None:
        turn_state = TurnState(id=TURN_STATE_ID)
        session.add(turn_state)
    return turn_state


def set_next_starter(session: Session, user_id: int | None) -> TurnState:
    """Implements /skip and winning — see MECHANICS.md's "Turn handoff"
    section. `None` opens the turn to anyone, cancels the win-turn
    reminder/expiry timers, and arms the 24h idle-autostart backstop
    (see IDLE_AUTOSTART_DELAY); a real user (re)schedules both win-turn
    timers, absolute from now, and clears the idle-autostart backstop —
    a designated human means the turn isn't "open to anyone" for that
    backstop's purposes. Returns the row so the command layer can
    schedule/cancel the actual JobQueue jobs (this module stays
    Telegram-agnostic)."""
    turn_state = get_or_create_turn_state(session)
    turn_state.next_starter_id = user_id
    if user_id is None:
        turn_state.reminder_at = None
        turn_state.expiry_at = None
        _mark_turn_opened(turn_state)
        logger.info("Turn opened — anyone may start the next game")
    else:
        now = datetime.now(UTC)
        turn_state.reminder_at = now + TURN_REMINDER_DELAY
        turn_state.expiry_at = now + TURN_EXPIRY_DELAY
        turn_state.turn_opened_at = None
        turn_state.autostart_deadline_at = None
        logger.info("Turn designated to player {}", user_id)
    return turn_state


def mark_turn_open_if_unassigned(session: Session) -> TurnState:
    """Arms the idle-autostart backstop for a game ending that leaves
    `next_starter_id` untouched (an unsolved/timeout ending — see
    MECHANICS.md's "Ending unsolved") rather than explicitly opening it
    via set_next_starter(session, None). A no-op (besides returning the
    row) if a specific player is still designated — mirrors the same
    "leave next_starter_id alone" rule those callers already follow, and
    is what lets jobs/timers/autostart.py's maybe_overthrow() defer to a
    designated human regardless of which call site invoked it."""
    turn_state = get_or_create_turn_state(session)
    if turn_state.next_starter_id is None:
        _mark_turn_opened(turn_state)
        logger.debug("Idle-autostart backstop armed — turn was already open")
    return turn_state


def _mark_turn_opened(turn_state: TurnState) -> None:
    now = datetime.now(UTC)
    turn_state.turn_opened_at = now
    turn_state.autostart_deadline_at = now + IDLE_AUTOSTART_DELAY


def clear_autostart(session: Session) -> None:
    """Cancels the idle-autostart backstop — called the moment any game
    (human- or bot-started) actually starts, so "nobody started a game
    for 24h" stays literally true regardless of who starts one. See
    commands/dm_start/_shared.py's _start_new_game() and
    jobs/timers/autostart.py's run_bot_autostart()."""
    turn_state = get_or_create_turn_state(session)
    turn_state.turn_opened_at = None
    turn_state.autostart_deadline_at = None
    logger.debug("Idle-autostart backstop cleared — a game just started")


def clear_turn_timers(session: Session) -> None:
    """Cancels the win-turn reminder/expiry deadlines without touching
    `next_starter_id` — used when the designated player actually starts
    their game, so a stale reminder doesn't fire after they've already
    acted."""
    turn_state = get_or_create_turn_state(session)
    turn_state.reminder_at = None
    turn_state.expiry_at = None
