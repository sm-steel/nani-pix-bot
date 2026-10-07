"""Weekly / monthly / yearly champions (spec §6): period math in the group
timezone, scoring from game_won events, and freezing a closed period."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
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
TOP_SIZE = 3
# Index = stage - 1 (normal) or turn - 1 (HARD MODE).
_WIN_POINTS = (5, 4, 3, 2, 1)
_HARD_POINTS = (6, 4)
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


def _win_points(event: LoggedEvent) -> int:
    table = _HARD_POINTS if event.data["hard_mode"] else _WIN_POINTS
    stage = event.data["stage"]
    return table[stage - 1] if 1 <= stage <= len(table) else 0


def _tally(board: dict[int, Standing], event: LoggedEvent) -> None:
    ended = _ended(event)
    if event.actor_id is not None:
        winner = board.setdefault(event.actor_id, Standing(event.actor_id))
        winner.add(_win_points(event), ended)
        winner.wins += 1
    if not event.data["hard_mode"] and event.subject_id is not None:
        board.setdefault(event.subject_id, Standing(event.subject_id)).add(HOST_POINTS, ended)


def score(won_events: Iterable[LoggedEvent], period: Period) -> list[Standing]:
    latest = {e.game_id: e for e in won_events}  # a refinish counts once
    board: dict[int, Standing] = {}
    for event in sorted(latest.values(), key=_ended):
        if period.start <= _ended(event) < period.end:
            _tally(board, event)
    ranked = (s for s in board.values() if s.score > 0)
    return sorted(ranked, key=lambda s: (-s.score, -s.wins, s.reached_at))


def _naive(at: datetime) -> datetime:
    # DATETIME columns hold naive UTC; compare like with like.
    return at.astimezone(UTC).replace(tzinfo=None)


def standings(session: Session, period: Period) -> list[Standing]:
    # Since the period's start, without an upper bound: a /setwinner re-finish
    # logs later but keeps the original ended_at (plan clarification 2).
    stmt = (
        select(EventLog)
        .where(
            EventLog.event_type == EventType.GAME_WON, EventLog.occurred_at >= _naive(period.start)
        )
        .order_by(EventLog.id)
    )
    return score([to_event(row) for row in session.scalars(stmt)], period)


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
