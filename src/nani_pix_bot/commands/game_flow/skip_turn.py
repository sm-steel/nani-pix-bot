"""What /skip does, whether typed (`/skip`, `/skip @user`) or tapped from its
menu (skip_picker.py): who may skip, and the two ways the turn moves on. See
MECHANICS.md's "Turn handoff" section."""

from loguru import logger
from sqlalchemy.orm import Session
from telegram.ext import ContextTypes

from nani_pix_bot.jobs import timers as timeout_module
from nani_pix_bot.models.turn_state import TurnState
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import players


def refusal_key(session: Session, user_id: int) -> str | None:
    """Why `user_id` may not /skip right now, as the reply's locale key —
    or None if they may. Only the designated starter may /skip, and only
    between games (MECHANICS.md's "Turn handoff"). Logs the refusal."""
    if game_service.active_or_setup_game(session) is not None:
        logger.warning("tried /skip while a game is running")
        return "skip.game_running"
    turn_state = game_service.get_turn_state(session)
    current = turn_state.next_starter_id if turn_state is not None else None
    if current is None:
        # An open turn names nobody, so there's nothing to hand off — not
        # a free-for-all where anyone may designate a starter (issue #235).
        logger.warning("tried /skip while the turn is open to anyone")
        return "skip.turn_open"
    if current != user_id:
        logger.warning(
            "tried /skip out of turn (designated: {next_starter})",
            next_starter=players.describe_player_id(session, current),
            next_starter_id=current,
        )
        return "skip.not_your_turn"
    return None


def candidates(session: Session, skipper_id: int, bot_id: int) -> list[int]:
    """Whom the menu offers the turn to, most active first: everyone `/skip
    @user` accepts (every known player but Nani Pix herself), minus the
    skipper, for whom a handoff would change nothing."""
    excluded = {skipper_id, bot_id}
    ids = [pid for pid in players.known_ids(session) if pid not in excluded]
    return players.by_activity(session, ids, None)


def open_turn(context: ContextTypes.DEFAULT_TYPE, session: Session) -> TurnState:
    """Open the turn to anyone. The caller schedules the idle autostart once
    the session has committed."""
    turn_state = game_service.set_next_starter(session, None, reason="/skip")
    timeout_module.cancel_turn_timers(context.job_queue)
    return turn_state


def hand_turn(context: ContextTypes.DEFAULT_TYPE, session: Session, target_id: int) -> None:
    turn_state = game_service.set_next_starter(session, target_id, reason="/skip")
    timeout_module.cancel_idle_autostart(context.job_queue)
    timeout_module.schedule_turn_timers(context.job_queue, turn_state)
