"""Progress for one-shot, hidden and group-unique achievements (spec §4).
One-shots return 1 once met, else 0; Explorer returns its k-of-5 count."""

from collections import defaultdict

from nani_pix_bot.models.enums import ClueKind, CurrencyReason, EventType, WinMethod
from nani_pix_bot.services.achievements.definitions import History
from nani_pix_bot.services.events import LoggedEvent
from nani_pix_bot.services.matching import MATCH_THRESHOLD

E = EventType
SPEED_SECONDS = 60
COMEBACK_WRONG = 3
CROWD_GUESSERS = 5
# So Close (hidden): a wrong guess within this many points under the threshold.
SO_CLOSE_MARGIN = 5.0
_ALL_KINDS = frozenset(k.value for k in ClueKind)


def _normal_wins(history: History) -> list[LoggedEvent]:
    return [e for e in history.mine(E.GAME_WON) if not e.data["hard_mode"]]


def _flag(condition: bool) -> int:
    return 1 if condition else 0


def kingmaker(history: History) -> int:
    return _flag(any(e.data["won"] for e in history.mine(E.VOTE_COUNTED)))


def clutch(history: History) -> int:
    return _flag(any(e.data["last_slot"] for e in _normal_wins(history)))


def first_try(history: History) -> int:
    return _flag(any(e.data["first_guess"] for e in _normal_wins(history)))


def _fast(event: LoggedEvent) -> bool:
    seconds = event.data.get("seconds")
    return seconds is not None and seconds <= SPEED_SECONDS


def speed_demon(history: History) -> int:
    return _flag(any(_fast(e) for e in _normal_wins(history)))


def comeback(history: History) -> int:
    return _flag(any(e.data["winner_wrong"] >= COMEBACK_WRONG for e in _normal_wins(history)))


def _crowded(event: LoggedEvent) -> bool:
    return not event.data["hard_mode"] and event.data["distinct_guessers"] >= CROWD_GUESSERS


def crowd_pleaser(history: History) -> int:
    return _flag(any(_crowded(e) for e in history.about_me(E.GAME_WON, E.GAME_UNSOLVED)))


def peoples_champion(history: History) -> int:
    return _flag(any(e.data["how"] == WinMethod.VOTE.value for e in history.mine(E.GAME_WON)))


def shopaholic(history: History) -> int:
    kinds: dict[int | None, set[str]] = defaultdict(set)
    for event in history.mine(E.CLUE_PURCHASED):
        kinds[event.game_id].add(event.data["kind"])
    return _flag(any(found >= _ALL_KINDS for found in kinds.values()))


def explorer(history: History) -> int:
    hosted = history.mine(E.GAME_ACTIVATED)
    return len({e.data["source"] for e in hosted if not e.data["hard_mode"]})


def dethroned(history: History) -> int:
    return _flag(bool(history.mine(E.OVERTHROWN)))


def _near_miss(event: LoggedEvent) -> bool:
    score = event.data.get("score")
    return score is not None and MATCH_THRESHOLD - SO_CLOSE_MARGIN <= score < MATCH_THRESHOLD


def so_close(history: History) -> int:
    return _flag(any(_near_miss(e) for e in history.mine(E.GUESS)))


def _penny(event: LoggedEvent) -> bool:
    return event.data["reason"] == CurrencyReason.TIP.value and event.data["amount"] == 1


def penny_pincher(history: History) -> int:
    return _flag(any(_penny(e) for e in history.mine(E.CURRENCY_MOVED)))


def group_wins(history: History) -> int:
    """Distinct games won by anyone — Pioneer (the 1st) and Milestone Keeper."""
    return len({e.game_id for e in history.group(E.GAME_WON)})
