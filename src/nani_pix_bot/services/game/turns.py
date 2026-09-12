"""TurnState bookkeeping — who starts the next game, and their win-turn
reminder/expiry timers. A related but distinct concern from the core
Game-row state machine in state.py — see MECHANICS.md's "Turn
handoff" section for the rules this implements."""

from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy.orm import Session

from nani_pix_bot.models.turn_state import TurnState

TURN_STATE_ID = 1

# Absolute from a turn being designated to a real user (a win, or
# /skip @user) — see MECHANICS.md's "Turn handoff" section.
TURN_REMINDER_DELAY = timedelta(minutes=15)
TURN_EXPIRY_DELAY = timedelta(hours=12)


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
    section. `None` opens the turn to anyone and cancels the win-turn
    reminder/expiry timers; a real user (re)schedules both, absolute
    from now. Returns the row so the command layer can schedule/cancel
    the actual JobQueue jobs (this module stays Telegram-agnostic)."""
    turn_state = get_or_create_turn_state(session)
    turn_state.next_starter_id = user_id
    if user_id is None:
        turn_state.reminder_at = None
        turn_state.expiry_at = None
        logger.info("Turn opened — anyone may start the next game")
    else:
        now = datetime.now(UTC)
        turn_state.reminder_at = now + TURN_REMINDER_DELAY
        turn_state.expiry_at = now + TURN_EXPIRY_DELAY
        logger.info("Turn designated to player {}", user_id)
    return turn_state


def clear_turn_timers(session: Session) -> None:
    """Cancels the win-turn reminder/expiry deadlines without touching
    `next_starter_id` — used when the designated player actually starts
    their game, so a stale reminder doesn't fire after they've already
    acted."""
    turn_state = get_or_create_turn_state(session)
    turn_state.reminder_at = None
    turn_state.expiry_at = None
