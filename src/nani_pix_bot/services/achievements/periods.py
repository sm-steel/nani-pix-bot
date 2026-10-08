"""Weekly / monthly / yearly champions (spec §6): period math in the group
timezone, scoring from game_won events, and freezing a closed period."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from types import MappingProxyType
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import EventType, PeriodType
from nani_pix_bot.models.event_log import EventLog
from nani_pix_bot.models.period import PeriodResult, PeriodState
from nani_pix_bot.services import players
from nani_pix_bot.services.achievements import engine, outbox
from nani_pix_bot.services.events import LoggedEvent, to_event

CHAMPION_KEYS: Mapping[PeriodType, str] = MappingProxyType(
    {
        PeriodType.WEEK: "champion_week",
        PeriodType.MONTH: "champion_month",
        PeriodType.YEAR: "champion_year",
    }
)
PERIOD_OF_CHAMPION: Mapping[str, PeriodType] = MappingProxyType(
    {key: ptype for ptype, key in CHAMPION_KEYS.items()}
)
TOP_SIZE = 3
# Index = stage - 1 (normal) or turn - 1 (HARD MODE).
WIN_POINTS = (5, 4, 3, 2, 1)
HARD_POINTS = (6, 4)
HOST_POINTS = 1
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class Period:
    type: PeriodType
    key: str
    start: datetime
    end: datetime  # exclusive


def _local_bounds(ptype: PeriodType, day: date) -> tuple[date, date, str]:
    if ptype is PeriodType.WEEK:
        start = day - timedelta(days=day.weekday())
        year, week, _ = start.isocalendar()
        return start, start + timedelta(days=7), f"{year}-W{week:02d}"
    if ptype is PeriodType.MONTH:
        start = day.replace(day=1)
        return start, (start + timedelta(days=32)).replace(day=1), f"{start:%Y-%m}"
    return date(day.year, 1, 1), date(day.year + 1, 1, 1), str(day.year)


def _midnight(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time(), tzinfo=tz).astimezone(UTC)


def period_at(ptype: PeriodType, at: datetime, tz: ZoneInfo) -> Period:
    start, end, key = _local_bounds(ptype, at.astimezone(tz).date())
    return Period(ptype, key, _midnight(start, tz), _midnight(end, tz))


def following(period: Period, tz: ZoneInfo) -> Period:
    return period_at(period.type, period.end, tz)


@dataclass
class Standing:
    player_id: int
    score: int = 0
    wins: int = 0
    reached_at: datetime = _EPOCH

    def add(self, points: int, at: datetime) -> None:
        self.score += points
        self.reached_at = at


def _ended(event: LoggedEvent) -> datetime:
    return datetime.fromisoformat(event.data["ended_at"])


def win_points(event: LoggedEvent) -> int:
    table = HARD_POINTS if event.data["hard_mode"] else WIN_POINTS
    stage = event.data["stage"]
    return table[stage - 1] if 1 <= stage <= len(table) else 0


class GainRole(StrEnum):
    WIN = "win"
    HOST = "host"


def _shares(event: LoggedEvent) -> list[tuple[int, int, GainRole]]:
    """Who one win pays and how much: the winner, and the host unless HARD MODE."""
    shares = []
    if event.actor_id is not None:
        shares.append((event.actor_id, win_points(event), GainRole.WIN))
    if not event.data["hard_mode"] and event.subject_id is not None:
        shares.append((event.subject_id, HOST_POINTS, GainRole.HOST))
    return shares


def _tally(board: dict[int, Standing], event: LoggedEvent) -> None:
    ended = _ended(event)
    for player_id, points, role in _shares(event):
        standing = board.setdefault(player_id, Standing(player_id))
        standing.add(points, ended)
        if role is GainRole.WIN:
            standing.wins += 1


def _counted(won_events: Iterable[LoggedEvent], period: Period) -> list[LoggedEvent]:
    """Each game's latest win (a refinish counts once) inside the period, in
    the order the games ended."""
    latest = {e.game_id: e for e in won_events}
    in_period = (e for e in latest.values() if period.start <= _ended(e) < period.end)
    return sorted(in_period, key=_ended)


def _ranked(board: dict[int, Standing]) -> list[Standing]:
    ranked = (s for s in board.values() if s.score > 0)
    return sorted(ranked, key=lambda s: (-s.score, -s.wins, s.reached_at))


def score(won_events: Iterable[LoggedEvent], period: Period) -> list[Standing]:
    board: dict[int, Standing] = {}
    for event in _counted(won_events, period):
        _tally(board, event)
    return _ranked(board)


@dataclass(frozen=True)
class Gain:
    """One player's share of one win, and where it moved them in the period."""

    player_id: int
    points: int
    role: GainRole
    game_id: int | None
    at: datetime
    stage: int
    hard_mode: bool
    rank_before: int | None  # None: no score in this period before the win
    rank_after: int | None


def gains(won_events: Iterable[LoggedEvent], period: Period) -> list[Gain]:
    """Every share of every win in the period, oldest first — the standings
    replayed one game at a time, so it always adds up to score()."""
    board: dict[int, Standing] = {}
    found: list[Gain] = []
    for event in _counted(won_events, period):
        before = _ranked(board)
        _tally(board, event)
        after = _ranked(board)
        found.extend(
            Gain(
                player_id=player_id,
                points=points,
                role=role,
                game_id=event.game_id,
                at=_ended(event),
                stage=event.data["stage"],
                hard_mode=bool(event.data["hard_mode"]),
                rank_before=_rank(before, player_id),
                rank_after=_rank(after, player_id),
            )
            for player_id, points, role in _shares(event)
        )
    return found


def _naive(at: datetime) -> datetime:
    # DATETIME columns hold naive UTC; compare like with like.
    return at.astimezone(UTC).replace(tzinfo=None)


def _won_events(session: Session, period: Period) -> list[LoggedEvent]:
    # Since the period's start, without an upper bound: a /setwinner re-finish
    # logs later but keeps the original ended_at (plan clarification 2).
    stmt = (
        select(EventLog)
        .where(
            EventLog.event_type == EventType.GAME_WON, EventLog.occurred_at >= _naive(period.start)
        )
        .order_by(EventLog.id)
    )
    return [to_event(row) for row in session.scalars(stmt)]


def standings(session: Session, period: Period) -> list[Standing]:
    return score(_won_events(session, period), period)


def recent_gains(session: Session, period: Period) -> list[Gain]:
    """The period's gains, newest first."""
    return gains(_won_events(session, period), period)[::-1]


@dataclass(frozen=True)
class PeriodGain:
    """What one win did to the winner's running total in one period."""

    period: Period
    player_id: int
    gain: int
    score: int
    rank_before: int | None  # None: no score in this period before the win
    rank_after: int


def win_event(session: Session, game_id: int) -> LoggedEvent | None:
    """The game's latest game_won event (a re-finish logs a second one)."""
    stmt = (
        select(EventLog)
        .where(EventLog.event_type == EventType.GAME_WON, EventLog.game_id == game_id)
        .order_by(EventLog.id.desc())
        .limit(1)
    )
    row = session.scalars(stmt).first()
    return to_event(row) if row is not None else None


def _rank(board: list[Standing], player_id: int) -> int | None:
    for rank, standing in enumerate(board, start=1):
        if standing.player_id == player_id:
            return rank
    return None


def _gain_in(session: Session, period: Period, won: LoggedEvent) -> PeriodGain | None:
    if won.actor_id is None or not period.start <= _ended(won) < period.end:
        return None
    events = _won_events(session, period)
    after = score(events, period)
    rank_after = _rank(after, won.actor_id)
    if rank_after is None:
        return None
    before = score([e for e in events if e.game_id != won.game_id], period)
    return PeriodGain(
        period=period,
        player_id=won.actor_id,
        gain=win_points(won),
        score=after[rank_after - 1].score,
        rank_before=_rank(before, won.actor_id),
        rank_after=rank_after,
    )


def win_gains(session: Session, game_id: int, now: datetime, tz: ZoneInfo) -> list[PeriodGain]:
    """The winner's gain in each running period (week, month, year) that the
    game's ended_at falls in; a re-finish long after a period closed skips it."""
    won = win_event(session, game_id)
    if won is None:
        return []
    found = (_gain_in(session, period_at(ptype, now, tz), won) for ptype in PeriodType)
    return [gain for gain in found if gain is not None]


def rank_of(session: Session, period: Period, player_id: int) -> int | None:
    return _rank(standings(session, period), player_id)


def finalize(session: Session, period: Period) -> list[Standing]:
    top = standings(session, period)[:TOP_SIZE]
    for rank, standing in enumerate(top, start=1):
        session.add(
            PeriodResult(
                period_type=period.type,
                period_key=period.key,
                rank=rank,
                player_id=standing.player_id,
                score=standing.score,
                wins=standing.wins,
            )
        )
    if top:
        session.flush()
        outbox.enqueue_period_summary(session, period.type.value, period.key)
        request = engine.GrantRequest(top[0].player_id, CHAMPION_KEYS[period.type], 1, period.key)
        engine.grant(session, request)
    logger.info(
        "closed {period_type} {period_key}: {count} ranked, champion {champion}",
        period_type=period.type.value,
        period_key=period.key,
        count=len(top),
        champion=players.describe_player_id(session, top[0].player_id) if top else "nobody",
    )
    return top


_ORDER: Mapping[PeriodType, int] = MappingProxyType(
    {PeriodType.WEEK: 0, PeriodType.MONTH: 1, PeriodType.YEAR: 2}
)


def _aware(at: datetime) -> datetime:
    return at.replace(tzinfo=UTC) if at.tzinfo is None else at


def _state(session: Session, ptype: PeriodType, now: datetime, tz: ZoneInfo) -> PeriodState:
    state = session.get(PeriodState, ptype.value)
    if state is None:
        running = period_at(ptype, now, tz)
        state = PeriodState(period_type=ptype, next_end=_naive(running.end))
        session.add(state)
        session.flush()
        logger.info(
            "{period_type} champions start with {period_key}",
            period_type=ptype.value,
            period_key=running.key,
        )
    return state


def _due_for(session: Session, ptype: PeriodType, now: datetime, tz: ZoneInfo) -> list[Period]:
    state = _state(session, ptype, now, tz)
    due: list[Period] = []
    while _aware(state.next_end) <= now:
        period = period_at(ptype, _aware(state.next_end) - timedelta(microseconds=1), tz)
        due.append(period)
        state.next_end = _naive(following(period, tz).end)
    return due


def finalize_due(session: Session, now: datetime, tz: ZoneInfo) -> list[Period]:
    due = [p for ptype in PeriodType for p in _due_for(session, ptype, now, tz)]
    due.sort(key=lambda p: (p.end, _ORDER[p.type]))
    for period in due:
        finalize(session, period)
    return due


def next_boundary(session: Session) -> datetime | None:
    return min((_aware(s.next_end) for s in session.scalars(select(PeriodState))), default=None)
