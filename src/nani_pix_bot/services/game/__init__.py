"""Public surface for the game-state-machine package — every existing
`from nani_pix_bot.services import game as game_service` call site
keeps working unchanged via attribute access on this re-exported API.

state.py holds the core Game-row state machine (plus the shared
display_title() helper); turns.py holds TurnState bookkeeping (who
starts next, their reminder/expiry timers) — a related but distinct
concern. get_or_create_player()/find_player_by_username() live in
services/players.py instead (pure Player-table operations, no Game
involved); seconds_until()/timeout_job_name()/setup_abandon_job_name()
live in jobs/timers.py instead (scheduling/naming helpers with no
Game-state-machine logic in them, used only there)."""

from nani_pix_bot.services.game.state import (
    SETUP_ABANDON_DELAY,
    STAGE_ORDER,
    TIMEOUT_DURATION,
    GuessOutcome,
    TitleVariants,
    activate_game,
    active_games,
    active_or_setup_game,
    can_start,
    clear_original_screenshot,
    create_setup_game,
    display_title,
    force_unsolved,
    force_win,
    get_setup_game_for_starter,
    match_candidates,
    prioritized_title,
    record_guess,
    setup_games,
    stage_manual_entry,
    stage_progress,
    stage_result,
)
from nani_pix_bot.services.game.turns import (
    TURN_EXPIRY_DELAY,
    TURN_REMINDER_DELAY,
    TURN_STATE_ID,
    clear_turn_timers,
    get_turn_state,
    set_next_starter,
)

__all__ = [
    "SETUP_ABANDON_DELAY",
    "STAGE_ORDER",
    "TIMEOUT_DURATION",
    "TURN_EXPIRY_DELAY",
    "TURN_REMINDER_DELAY",
    "TURN_STATE_ID",
    "GuessOutcome",
    "TitleVariants",
    "activate_game",
    "active_games",
    "active_or_setup_game",
    "can_start",
    "clear_original_screenshot",
    "clear_turn_timers",
    "create_setup_game",
    "display_title",
    "force_unsolved",
    "force_win",
    "get_setup_game_for_starter",
    "get_turn_state",
    "match_candidates",
    "prioritized_title",
    "record_guess",
    "set_next_starter",
    "setup_games",
    "stage_manual_entry",
    "stage_progress",
    "stage_result",
]
