"""Progress of the generic per-season achievements (seasons spec §6), read
from a season-scoped History (services/achievements/season_eval.py)."""

from collections.abc import Callable

from nani_pix_bot.models.enums import CurrencyReason, EventType
from nani_pix_bot.seasons.definition import RewardKind
from nani_pix_bot.services.achievements.definitions import SeasonHistory
from nani_pix_bot.services.events import LoggedEvent

E = EventType
PLAYED = (E.GUESS, E.GAME_ACTIVATED)


def _flag(condition: bool) -> int:
    return 1 if condition else 0


def _own(history: SeasonHistory, event_type: EventType) -> list[LoggedEvent]:
    """Season-owned events of THIS season (the scope admits every season's, E6)."""
    return [e for e in history.mine(event_type) if e.data.get("season_id") == history.season_id]


def debut(history: SeasonHistory) -> int:
    return _flag(bool(history.my_days(*PLAYED)))


def opening_act(history: SeasonHistory) -> int:
    return _flag(history.first_day in history.my_days(*PLAYED))


def curtain_call(history: SeasonHistory) -> int:
    return _flag(history.last_day in history.my_days(*PLAYED))


def pass_complete(history: SeasonHistory) -> int:
    return _flag(any(not e.data["premium"] for e in _own(history, E.TRACK_COMPLETED)))


def pass_premium(history: SeasonHistory) -> int:
    return _flag(any(e.data["premium"] for e in _own(history, E.TRACK_COMPLETED)))


def window_shopper(history: SeasonHistory) -> int:
    shop = CurrencyReason.SEASON_SHOP.value
    return _flag(
        any(
            e.data.get("reason") == shop and e.data.get("season_id") == history.season_id
            for e in history.mine(E.CURRENCY_MOVED)
        )
    )


def collector(history: SeasonHistory) -> int:
    run = history.run
    selling = {f"{run.run_id}/{o.item_key}" for o in run.shop if o.kind is RewardKind.ITEM}
    owned = {e.data["item_id"] for e in history.mine(E.ITEM_GAINED)}
    return _flag(bool(selling) and selling <= owned)


def _rank(history: SeasonHistory) -> int | None:
    ended = _own(history, E.SEASON_ENDED)
    return ended[-1].data["rank"] if ended else None


def podium(place: int) -> Callable[[SeasonHistory], int]:
    def progress(history: SeasonHistory) -> int:
        return _flag(_rank(history) == place)

    return progress


def on_theme(history: SeasonHistory) -> int:
    return len(history.mine(E.GAME_WON)) if history.run.gate is not None else 0
