"""What an achievement is (spec §3-4): a Definition plus the pure tier math
the engine and the views share. Definitions live in code (catalogue.py);
only grants live in the DB."""

import enum
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Protocol
from zoneinfo import ZoneInfo

from nani_pix_bot.models.enums import EventType, Rarity
from nani_pix_bot.services.events import LoggedEvent

if TYPE_CHECKING:
    from nani_pix_bot.seasons.definition import SeasonRun


class Kind(enum.Enum):
    ONE_SHOT = "one_shot"
    LADDER = "ladder"
    GROUP_UNIQUE = "group_unique"  # one holder per tier, ever
    PERIOD = "period"  # granted by the period job, keyed by period
    SEASON = "season"  # once per season, period_key = the season's run_id


class History(Protocol):
    """One player's view of the event log, read by progress functions:
    events they caused, events about them, and everyone's."""

    player_id: int
    tz: ZoneInfo

    def mine(self, *types: EventType) -> list[LoggedEvent]: ...

    def about_me(self, *types: EventType) -> list[LoggedEvent]: ...

    def group(self, *types: EventType) -> list[LoggedEvent]: ...

    # Local dates (in `tz`) of my events of these types — a bot-started
    # (HARD MODE) activation left out, as it is no one's hosting. Narrower
    # than mine(): only timestamps are read.
    def my_days(self, *types: EventType) -> set[date]: ...

    # Whether anyone did any of `types` on a local day from `first` to
    # `last` (inclusive), with the same HARD MODE exclusion.
    def group_active_between(
        self, types: tuple[EventType, ...], first: date, last: date
    ) -> bool: ...


class SeasonHistory(History, Protocol):
    """A History scoped to one season (services/seasons/season_history.py), plus
    what season progress needs to know about it."""

    season_id: int
    run: "SeasonRun"
    first_day: date
    last_day: date


ProgressFn = Callable[[History], int]


@dataclass(frozen=True)
class Definition:
    key: str
    kind: Kind
    tiers: tuple[int, ...]
    rarities: tuple[Rarity, ...]
    triggers: frozenset[EventType]
    progress: ProgressFn
    endless_step: int | None = None
    hidden: bool = False
    titled: bool = False  # SEASON only: whether earning it confers a title
    run_id: str | None = None  # counts only in this run's season

    def __post_init__(self) -> None:
        if self.titled and self.kind is not Kind.SEASON:
            msg = "only season achievements grant a title by flag"
            raise ValueError(msg)
        if not self.tiers or len(self.tiers) != len(self.rarities):
            msg = f"{self.key}: needs one of rarities per tier"
            raise ValueError(msg)
        if any(a >= b for a, b in zip(self.tiers, self.tiers[1:], strict=False)):
            msg = f"{self.key}: tiers must be strictly increasing"
            raise ValueError(msg)


_B, _S, _G, _P = Rarity.BRONZE, Rarity.SILVER, Rarity.GOLD, Rarity.PLATINUM
# Plan clarification 4: one step up per tier, always ending on Platinum.
_CLIMBS: dict[int, tuple[Rarity, ...]] = {
    2: (_G, _P),
    3: (_B, _G, _P),
    4: (_B, _S, _G, _P),
    5: (_B, _S, _S, _G, _P),
    6: (_B, _B, _S, _S, _G, _P),
}


def climb(n: int) -> tuple[Rarity, ...]:
    return _CLIMBS[n]


def threshold(defn: Definition, tier: int) -> int:
    defined = len(defn.tiers)
    if tier <= defined:
        return defn.tiers[tier - 1]
    if defn.endless_step is None:
        msg = f"{defn.key} has no tier {tier}"
        raise ValueError(msg)
    return defn.tiers[-1] + defn.endless_step * (tier - defined)


def next_threshold(defn: Definition, tier: int) -> int | None:
    """The threshold of the tier after `tier`, None once there is none."""
    if tier >= len(defn.tiers) and defn.endless_step is None:
        return None
    return threshold(defn, tier + 1)


def reached(defn: Definition, value: int) -> int:
    """How many tiers `value` has reached, endless ones included."""
    count = sum(1 for t in defn.tiers if value >= t)
    if count == len(defn.tiers) and defn.endless_step:
        count += (value - defn.tiers[-1]) // defn.endless_step
    return count


def exact_tier(defn: Definition, value: int) -> int | None:
    """The tier whose threshold is exactly `value` — how a group-unique
    milestone finds the one game that crossed it."""
    tier = reached(defn, value)
    if tier == 0 or threshold(defn, tier) != value:
        return None
    return tier


def rarity_of(defn: Definition, tier: int) -> Rarity:
    return defn.rarities[min(tier, len(defn.rarities)) - 1]


def grants_title(defn: Definition, tier: int) -> bool:
    """The prestige set (spec §4): group-unique, champions, and the top
    *defined* tier of every ladder; a season achievement when it says so."""
    if defn.kind is Kind.SEASON:
        return defn.titled
    if defn.kind in (Kind.GROUP_UNIQUE, Kind.PERIOD):
        return True
    return defn.kind is Kind.LADDER and tier == len(defn.tiers)
