"""Where one player stands on every achievement (spec §5) — the view model
behind /achievements, plus the achievements top. Telegram-free."""

import enum
from collections.abc import Callable, Iterable, Mapping, Set
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.achievement import AchievementClaim, AchievementGrant
from nani_pix_bot.models.enums import SeasonStatus
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import SeasonRun
from nani_pix_bot.services import settings
from nani_pix_bot.services.achievements import catalogue, periods, season_eval
from nani_pix_bot.services.achievements.definitions import (
    Definition,
    Kind,
    SeasonHistory,
    next_threshold,
    reached,
)
from nani_pix_bot.services.achievements.history import DbHistory
from nani_pix_bot.services.seasons import schedule


class State(enum.Enum):
    EARNED = "earned"
    RACE = "race"  # a champion period in progress, and the player is on the board
    PARTIAL = "partial"
    UNTOUCHED = "untouched"
    TAKEN = "taken"  # a group-unique someone else holds
    HIDDEN = "hidden"


class View(enum.StrEnum):
    ALL = "a"
    EARNED = "e"
    NOT_YET = "n"


@dataclass(frozen=True)
class Status:
    defn: Definition
    state: State
    tier: int = 0  # highest tier held; times won for a champion, seasons for a season one
    value: int = 0
    target: int | None = None
    granted_at: datetime | None = None
    period_key: str = ""
    holder_id: int | None = None
    rank: int | None = None

    @property
    def fraction(self) -> float:
        return self.value / self.target if self.target else 0.0


@dataclass(frozen=True)
class _OpenSeason:
    run: SeasonRun
    history: SeasonHistory


@dataclass(frozen=True)
class _Context:
    session: Session
    history: DbHistory
    grants: tuple[AchievementGrant, ...]
    now: datetime
    open_season: _OpenSeason | None = None


def _earned(defn: Definition, ctx: _Context) -> Status:
    latest = max(ctx.grants, key=lambda g: g.granted_at)
    if defn.kind is Kind.PERIOD:
        return Status(
            defn,
            State.EARNED,
            tier=len(ctx.grants),
            granted_at=latest.granted_at,
            period_key=latest.period_key,
            rank=_current_rank(defn, ctx),  # a past champion still races this period
        )
    if defn.kind is Kind.SEASON:
        return Status(
            defn,
            State.EARNED,
            tier=len({g.period_key for g in ctx.grants}),
            granted_at=latest.granted_at,
            period_key=latest.period_key,
        )
    tier = max(g.tier for g in ctx.grants)
    return Status(
        defn,
        State.EARNED,
        tier=tier,
        value=defn.progress(ctx.history),
        target=next_threshold(defn, tier),
        granted_at=latest.granted_at,
    )


def _current_rank(defn: Definition, ctx: _Context) -> int | None:
    ptype = periods.PERIOD_OF_CHAMPION[defn.key]
    period = periods.period_at(ptype, ctx.now, ctx.history.tz)
    return periods.rank_of(ctx.session, period, ctx.history.player_id)


def _race(defn: Definition, ctx: _Context) -> Status:
    rank = _current_rank(defn, ctx)
    return Status(defn, State.RACE if rank else State.UNTOUCHED, rank=rank)


def _unclaimed(defn: Definition, ctx: _Context) -> Status:
    claim = ctx.session.get(AchievementClaim, (defn.key, 1))
    if len(defn.tiers) == 1 and claim is not None:
        return Status(defn, State.TAKEN, holder_id=claim.player_id)
    value = defn.progress(ctx.history)
    return Status(
        defn, State.UNTOUCHED, value=value, target=next_threshold(defn, reached(defn, value))
    )


def _plain(defn: Definition, ctx: _Context) -> Status:
    if defn.hidden:
        return Status(defn, State.HIDDEN)
    value = defn.progress(ctx.history)
    state = State.PARTIAL if value > 0 else State.UNTOUCHED
    return Status(defn, state, value=value, target=defn.tiers[0])


def _open_season(session: Session, player_id: int) -> _OpenSeason | None:
    """The season that still grants (active or closing) and the player's
    history scoped to it, built once per build() for every SEASON definition."""
    season = schedule.current(session)
    if season is None or season.status not in (SeasonStatus.ACTIVE, SeasonStatus.CLOSING):
        return None
    run = registry.get(season.run_id)
    if run is None:
        return None
    return _OpenSeason(run, season_eval.scoped_history(session, season, run, player_id))


def _season_progress(defn: Definition, ctx: _Context) -> Status:
    """Progress in the open season, if it is one this definition counts in."""
    if defn.hidden:
        return Status(defn, State.HIDDEN)
    open_season = ctx.open_season
    if open_season is None or not season_eval.counts_in(defn, open_season.run):
        return Status(defn, State.UNTOUCHED, target=defn.tiers[0])
    value = defn.progress(open_season.history)
    state = State.PARTIAL if value > 0 else State.UNTOUCHED
    return Status(defn, state, value=value, target=defn.tiers[0])


_UNEARNED: Mapping[Kind, Callable[[Definition, _Context], Status]] = MappingProxyType(
    {Kind.PERIOD: _race, Kind.GROUP_UNIQUE: _unclaimed, Kind.SEASON: _season_progress}
)


def _status(defn: Definition, ctx: _Context) -> Status:
    if ctx.grants:
        return _earned(defn, ctx)
    return _UNEARNED.get(defn.kind, _plain)(defn, ctx)


def _grants(session: Session, player_id: int) -> dict[str, list[AchievementGrant]]:
    held: dict[str, list[AchievementGrant]] = {}
    for row in session.scalars(
        select(AchievementGrant).where(AchievementGrant.player_id == player_id)
    ):
        held.setdefault(row.key, []).append(row)
    return held


def _listed(defn: Definition, open_season: _OpenSeason | None, held_keys: Set[str]) -> bool:
    """A run's own achievement is listed only while its season is the open
    one, or for a player holding it; every other definition always is."""
    if defn.run_id is None or defn.key in held_keys:
        return True
    return open_season is not None and open_season.run.run_id == defn.run_id


def _held_keys(session: Session, player_id: int) -> set[str]:
    stmt = select(AchievementGrant.key).where(AchievementGrant.player_id == player_id)
    return set(session.scalars(stmt))


def build(
    session: Session, player_id: int, now: datetime, *, alongside: int | None = None
) -> list[Status]:
    """Where the player stands on every achievement listed for them. With
    `alongside` (compare), a run's achievement the other player holds is
    listed too, so both sides build the same definitions in the same order."""
    history = DbHistory(session, player_id, settings.get_group_timezone(session))
    held = _grants(session, player_id)
    open_season = _open_season(session, player_id)
    held_keys = set(held) | (_held_keys(session, alongside) if alongside is not None else set())
    return [
        _status(defn, _Context(session, history, tuple(held.get(defn.key, ())), now, open_season))
        for defn in catalogue.definitions()
        if _listed(defn, open_season, held_keys)
    ]


_SECTIONS: Mapping[View, tuple[tuple[State, ...], ...]] = MappingProxyType(
    {
        View.ALL: (
            (State.EARNED,),
            (State.RACE,),
            (State.PARTIAL,),
            (State.UNTOUCHED, State.TAKEN),
            (State.HIDDEN,),
        ),
        View.EARNED: ((State.EARNED,), (State.PARTIAL,)),
        View.NOT_YET: (
            (State.RACE,),
            (State.PARTIAL,),
            (State.UNTOUCHED, State.TAKEN),
            (State.HIDDEN,),
        ),
    }
)


def newest_first(items: Iterable[Status]) -> list[Status]:
    """Latest grant first; ties (and ungranted items) keep their order."""
    return sorted(items, key=lambda s: (s.granted_at is not None, s.granted_at), reverse=True)


def _sort_key(item: Status) -> float:
    """Closest to done among partial; everything else keeps catalogue order
    (the sort is stable). Earned sections go through newest_first instead."""
    return -item.fraction if item.state is State.PARTIAL else 0.0


def sections(items: Iterable[Status], view: View) -> list[tuple[State, list[Status]]]:
    pool = list(items)
    out: list[tuple[State, list[Status]]] = []
    for states in _SECTIONS[view]:
        members = sorted((s for s in pool if s.state in states), key=_sort_key)
        if states == (State.EARNED,):
            members = newest_first(members)
        if members:
            out.append((states[0], members))
    return out


def ordered(items: Iterable[Status], view: View) -> list[Status]:
    return [s for _, members in sections(items, view) for s in members]


@dataclass(frozen=True)
class TopRow:
    player_id: int
    points: int
    count: int


def _top_stmt(exclude: int | None = None):
    points = func.sum(AchievementGrant.points)
    stmt = select(AchievementGrant.player_id, points, func.count(AchievementGrant.id))
    if exclude is not None:
        stmt = stmt.where(AchievementGrant.player_id != exclude)
    return stmt.group_by(AchievementGrant.player_id).order_by(
        points.desc(), func.max(AchievementGrant.granted_at), AchievementGrant.player_id
    )


def top(
    session: Session, *, limit: int, offset: int = 0, exclude: int | None = None
) -> list[TopRow]:
    """Players with at least one grant, best first; `exclude` leaves one out."""
    rows = session.execute(_top_stmt(exclude).limit(limit).offset(offset))
    return [TopRow(player_id, int(points), int(count)) for player_id, points, count in rows]


def ranked_count(session: Session, *, exclude: int | None = None) -> int:
    stmt = select(func.count(func.distinct(AchievementGrant.player_id)))
    if exclude is not None:
        stmt = stmt.where(AchievementGrant.player_id != exclude)
    return int(session.scalar(stmt) or 0)


def rank_of(session: Session, player_id: int) -> int | None:
    for rank, (pid, _points, _count) in enumerate(session.execute(_top_stmt()), start=1):
        if pid == player_id:
            return rank
    return None


def points_of(session: Session, player_id: int) -> int:
    stmt = select(func.coalesce(func.sum(AchievementGrant.points), 0)).where(
        AchievementGrant.player_id == player_id
    )
    return int(session.scalar(stmt) or 0)


class CompareFilter(enum.StrEnum):
    ALL = "a"
    THEIRS = "t"  # they have it, I don't
    MINE = "m"  # I have it, they don't


def _has(item: Status) -> bool:
    return item.state is State.EARNED


def compare(
    mine: list[Status], theirs: list[Status], filt: CompareFilter
) -> list[tuple[Status, Status]]:
    """Both lists come from build(), so both are in catalogue order."""
    pairs = list(zip(mine, theirs, strict=True))
    if filt is CompareFilter.THEIRS:
        return [(m, t) for m, t in pairs if _has(t) and not _has(m)]
    if filt is CompareFilter.MINE:
        return [(m, t) for m, t in pairs if _has(m) and not _has(t)]
    return pairs
