"""Progress toward a season objective's goal (seasons spec §5), read from a
season-scoped History (season_history.py). Shared by every run module."""

from collections.abc import Callable, Mapping
from types import MappingProxyType

from nani_pix_bot.models.enums import CurrencyReason, EventType
from nani_pix_bot.seasons.definition import Goal, GoalKind
from nani_pix_bot.services import i18n
from nani_pix_bot.services.achievements.definitions import History

E = EventType

TRIGGERS: Mapping[GoalKind, tuple[EventType, ...]] = MappingProxyType(
    {
        GoalKind.HOSTED: (E.GAME_ACTIVATED,),
        GoalKind.WINS: (E.GAME_WON,),
        GoalKind.GUESSED_IN: (E.GUESS,),
        GoalKind.SHARED_CLUES: (E.CLUE_SHARED,),
        GoalKind.TIPS: (E.CURRENCY_MOVED,),
        GoalKind.DAYS_PLAYED: (E.GUESS, E.GAME_ACTIVATED),
    }
)


def _hosted(history: History) -> int:
    # A bot-started (HARD MODE) game is no one's hosting.
    return sum(1 for e in history.mine(E.GAME_ACTIVATED) if not e.data.get("hard_mode"))


def _wins(max_stage: int | None, history: History) -> int:
    wins = history.mine(E.GAME_WON)
    if max_stage is None:
        return len(wins)
    by_stage = range(1, max_stage + 1)
    return sum(1 for e in wins if not e.data.get("hard_mode") and e.data.get("stage") in by_stage)


def _guessed_in(history: History) -> int:
    return len({e.game_id for e in history.mine(E.GUESS)})


def _shared(history: History) -> int:
    return len(history.mine(E.CLUE_SHARED))


def _tips(history: History) -> int:
    tip = CurrencyReason.TIP.value
    return sum(1 for e in history.mine(E.CURRENCY_MOVED) if e.data.get("reason") == tip)


def _days(history: History) -> int:
    return len(history.my_days(E.GUESS, E.GAME_ACTIVATED))


# Every kind but WINS, whose stage limit makes it read the goal too.
_COUNTS: Mapping[GoalKind, Callable[[History], int]] = MappingProxyType(
    {
        GoalKind.HOSTED: _hosted,
        GoalKind.GUESSED_IN: _guessed_in,
        GoalKind.SHARED_CLUES: _shared,
        GoalKind.TIPS: _tips,
        GoalKind.DAYS_PLAYED: _days,
    }
)


def progress(goal: Goal, history: History) -> int:
    """The raw count toward `goal`, not capped at its target."""
    if goal.kind is GoalKind.WINS:
        return _wins(goal.max_stage, history)
    return _COUNTS[goal.kind](history)


def describe(goal: Goal, lang: str) -> str:
    key = f"season_objective.goal.{goal.kind.value}"
    if goal.kind is GoalKind.WINS and goal.max_stage is not None:
        key += "_by_stage"
    # Every template gets the same kwargs; str.format ignores the unused `stage`.
    return i18n.t(key, lang, n=goal.target, stage=goal.max_stage)
