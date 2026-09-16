"""Scheduling primitives shared by every timer submodule in this
package — pure datetime math, no Telegram/DB access of its own."""

from datetime import UTC, datetime

from nani_pix_bot.models.game import Game


def seconds_until(deadline: datetime | None) -> float:
    """Seconds from now until `deadline`, clamped at 0 for an already-
    overdue deadline (or if there's no deadline at all). Normalizes naive
    datetimes (as DATETIME columns round-trip from the DB) to UTC before
    comparing — shared by the game timeout, setup-abandon, turn, and
    inactivity timers."""
    if deadline is None:
        return 0.0
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    return max((deadline - datetime.now(UTC)).total_seconds(), 0.0)


def seconds_until_timeout(game: Game) -> float:
    """Seconds from now until `game.scheduled_end_at` — see seconds_until()."""
    return seconds_until(game.scheduled_end_at)
