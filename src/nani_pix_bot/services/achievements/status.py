"""Where one player stands on every achievement (spec §5) — the view model
behind /achievements, plus the achievements top. Telegram-free."""

import enum
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.achievement import AchievementClaim, AchievementGrant
from nani_pix_bot.services import settings
from nani_pix_bot.services.achievements import catalogue, periods
from nani_pix_bot.services.achievements.definitions import Definition, Kind, next_threshold, reached
from nani_pix_bot.services.achievements.history import DbHistory


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
    tier: int = 0  # highest tier held; times won for a champion
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
class _Context:
    session: Session
    history: DbHistory
    grants: tuple[AchievementGrant, ...]
    now: datetime


def _earned(defn: Definition, ctx: _Context) -> Status:
    latest = max(ctx.grants, key=lambda g: g.granted_at)
    if defn.kind is Kind.PERIOD:
        return Status(
            defn,
            State.EARNED,
            tier=len(ctx.grants),
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


def _race(defn: Definition, ctx: _Context) -> Status:
    ptype = periods.PERIOD_OF_CHAMPION[defn.key]
    period = periods.period_at(ptype, ctx.now, ctx.history.tz)
    rank = periods.rank_of(ctx.session, period, ctx.history.player_id)
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


_UNEARNED: Mapping[Kind, Callable[[Definition, _Context], Status]] = MappingProxyType(
    {Kind.PERIOD: _race, Kind.GROUP_UNIQUE: _unclaimed}
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


def build(session: Session, player_id: int, now: datetime) -> list[Status]:
    history = DbHistory(session, player_id, settings.get_group_timezone(session))
    held = _grants(session, player_id)
    return [
        _status(defn, _Context(session, history, tuple(held.get(defn.key, ())), now))
        for defn in catalogue.CATALOGUE
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


def _top_stmt():
    points = func.sum(AchievementGrant.points)
    return (
        select(AchievementGrant.player_id, points, func.count(AchievementGrant.id))
        .group_by(AchievementGrant.player_id)
        .order_by(points.desc(), func.max(AchievementGrant.granted_at), AchievementGrant.player_id)
    )


def top(session: Session, *, limit: int, offset: int = 0) -> list[TopRow]:
    rows = session.execute(_top_stmt().limit(limit).offset(offset))
    return [TopRow(player_id, int(points), int(count)) for player_id, points, count in rows]


def ranked_count(session: Session) -> int:
    stmt = select(func.count(func.distinct(AchievementGrant.player_id)))
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
