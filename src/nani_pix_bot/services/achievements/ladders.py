"""Progress for every counter ladder (spec §4). Each function takes one
player's History and returns an int; reversals never count (spec §2).
Pure: no DB, no clock."""

from collections.abc import Iterable
from datetime import date, timedelta

from nani_pix_bot.models.enums import CurrencyReason, EventType
from nani_pix_bot.services.achievements.definitions import History
from nani_pix_bot.services.events import LoggedEvent

E = EventType
_SPENDS = frozenset({CurrencyReason.CLUE_PURCHASE.value, CurrencyReason.SHARPEN.value})
_TIP = CurrencyReason.TIP.value
_BOUNTY_WIN = CurrencyReason.BOUNTY_WIN.value
_PROMPT_TURN = CurrencyReason.PROMPT_TURN.value
_ACTIVE = (E.GUESS, E.CLUE_PURCHASED, E.VOTE_COUNTED, E.GAME_ACTIVATED)
_STUMP_GUESSERS = 3
_DAY = timedelta(days=1)


def _games(events: Iterable[LoggedEvent]) -> int:
    return len({e.game_id for e in events})


def _money(history: History, *, incoming: bool) -> list[LoggedEvent]:
    moved = history.about_me(E.CURRENCY_MOVED) if incoming else history.mine(E.CURRENCY_MOVED)
    return [e for e in moved if not e.data["reversal"]]


def _amounts(events: Iterable[LoggedEvent], reasons: frozenset[str]) -> list[int]:
    return [e.data["amount"] for e in events if e.data["reason"] in reasons]


def wins(history: History) -> int:
    return _games(history.mine(E.GAME_WON))


def hosted_solved(history: History) -> int:
    return _games(e for e in history.about_me(E.GAME_WON) if not e.data["hard_mode"])


def games_guessed(history: History) -> int:
    return _games(history.mine(E.GUESS))


def earned(history: History) -> int:
    return sum(e.data["amount"] for e in _money(history, incoming=True))


def spent(history: History) -> int:
    direct = sum(_amounts(_money(history, incoming=False), _SPENDS))
    return direct + sum(e.data["amount"] for e in history.mine(E.BOUNTY_SETTLED))


def clues_bought(history: History) -> int:
    return len(history.mine(E.CLUE_PURCHASED))


def tipped(history: History) -> int:
    return sum(_amounts(_money(history, incoming=False), frozenset({_TIP})))


def active_days(history: History) -> int:
    return len(history.my_days(*_ACTIVE))


def _continues(history: History, before: date, day: date) -> bool:
    """Is `day` the next *game day* after `before` (no day anyone played
    in between — plan clarification 1)?"""
    if day - before == _DAY:
        return True
    return not history.group_active_between(_ACTIVE, before + _DAY, day - _DAY)


def longest_streak(history: History) -> int:
    """Longest run of consecutive game days on every one of which this
    player played. Every day I played is a game day, so a run only needs
    the group's history in the gaps between my days."""
    best = run = 0
    before: date | None = None
    for day in sorted(history.my_days(*_ACTIVE)):
        run = run + 1 if before is not None and _continues(history, before, day) else 1
        best = max(best, run)
        before = day
    return best


def hard_mode_wins(history: History) -> int:
    return _games(e for e in history.mine(E.GAME_WON) if e.data["hard_mode"])


def ballots(history: History) -> int:
    return _games(history.mine(E.VOTE_COUNTED))


def stage_one_wins(history: History) -> int:
    return _games(e for e in history.mine(E.GAME_WON) if e.data["stage"] == 1)


def _stumping(event: LoggedEvent) -> bool:
    return not event.data["hard_mode"] and event.data["distinct_guessers"] >= _STUMP_GUESSERS


def stumped(history: History) -> int:
    return _games(e for e in history.about_me(E.GAME_UNSOLVED) if _stumping(e))


def _finished_games(history: History) -> list[LoggedEvent]:
    """One terminal event per game, in the order games first ended, carrying
    the game's *last* outcome (a re-finish turns unsolved into won)."""
    last: dict[int | None, LoggedEvent] = {}
    for event in history.group(E.GAME_WON, E.GAME_UNSOLVED):
        last[event.game_id] = event  # dicts keep first-insertion order
    return list(last.values())


def win_streak(history: History) -> int:
    best = run = 0
    for event in _finished_games(history):
        if event.subject_id == history.player_id:
            continue  # a game I hosted can't be mine to win
        mine = event.event_type is E.GAME_WON and event.actor_id == history.player_id
        run = run + 1 if mine else 0
        best = max(best, run)
    return best


def biggest_pot(history: History) -> int:
    return max(_amounts(_money(history, incoming=True), frozenset({_BOUNTY_WIN})), default=0)


def purist_wins(history: History) -> int:
    bought_in = {e.game_id for e in history.mine(E.CLUE_PURCHASED)}
    wins_ = history.mine(E.GAME_WON)
    return _games(e for e in wins_ if e.data["hard_mode"] and e.game_id not in bought_in)


def warm_guesses(history: History) -> int:
    return sum(1 for e in history.mine(E.GUESS) if e.data.get("reveal_new"))


def prompt_starts(history: History) -> int:
    return len(_amounts(_money(history, incoming=True), frozenset({_PROMPT_TURN})))
