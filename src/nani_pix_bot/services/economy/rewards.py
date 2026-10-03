"""Pure pixel-reward math — no DB, no Telegram; deterministic given its
inputs (CLAUDE.md: game logic as pure functions). earning.py feeds it
amounts from config.get_amounts() and facts read from the DB."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from nani_pix_bot.services.economy.config import WIN_STAGE_KEYS, EconomyKey

# Setter bonus: solved at stage 2 to 4 (not stage 1, too easy; not
# stage 5, barely solvable). Unsolved games pay the setter nothing.
SETTER_REWARD_STAGES = range(2, 5)

# Prompt-turn bonus: game created within this long of receiving the turn.
PROMPT_TURN_WINDOW = timedelta(hours=1)


def win_reward(amounts: Mapping[EconomyKey, int], *, stage_number: int, multiplier: int = 1) -> int:
    if not 1 <= stage_number <= len(WIN_STAGE_KEYS):
        msg = f"stage number out of range: {stage_number}"
        raise ValueError(msg)
    return amounts[WIN_STAGE_KEYS[stage_number - 1]] * multiplier


def wrong_guess_reward(amounts: Mapping[EconomyKey, int], *, earned_this_game: int) -> int:
    """`min(step, cap - earned)`, floored at 0 — stops exactly at the cap
    even when the cap isn't a multiple of the step, and stays 0 if an
    admin lowered the cap below what was already earned."""
    remaining = amounts[EconomyKey.WRONG_GUESS_CAP] - earned_this_game
    return max(0, min(amounts[EconomyKey.WRONG_GUESS], remaining))


def setter_rewarded(stage_number: int) -> bool:
    return stage_number in SETTER_REWARD_STAGES


def _as_utc(at: datetime) -> datetime:
    # DATETIME columns round-trip from the DB naive — they're UTC by convention.
    return at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)


def is_prompt_start(*, created_at: datetime, turn_received_at: datetime) -> bool:
    elapsed = _as_utc(created_at) - _as_utc(turn_received_at)
    return timedelta(0) <= elapsed <= PROMPT_TURN_WINDOW
