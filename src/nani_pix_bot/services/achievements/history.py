"""History backed by event_log — what progress functions read in production.

One instance serves one evaluation pass (engine.on_event builds a fresh one
per player per event, status.build one per view), so every read is memoised
per (kind, types) for the instance's lifetime: a /guess asks for the same
GUESS rows from several definitions. Histories built together by
for_players() also share their group-wide reads, which are the same for
everyone (a reward cascade only appends currency_moved rows, which no
group-wide read asks for)."""

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Self
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, Select, Text, and_, select, type_coerce
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import EventType
from nani_pix_bot.models.event_log import EventLog
from nani_pix_bot.services.events import LoggedEvent, as_utc, to_event

_Types = tuple[EventType, ...]
_HOSTED = EventLog.event_type == EventType.GAME_ACTIVATED
# Plain columns, not ORM entities: a year of history is tens of thousands
# of rows, and identity-mapping each one costs more than the query.
_COLUMNS = (
    EventLog.id,
    EventLog.event_type,
    EventLog.actor_id,
    EventLog.subject_id,
    EventLog.game_id,
    # Raw JSON text, decoded only by the events somebody looks into.
    type_coerce(EventLog.data, Text).label("data"),
    EventLog.occurred_at,
)


class DbHistory:
    def __init__(self, session: Session, player_id: int, tz: ZoneInfo) -> None:
        self._session = session
        self.player_id = player_id
        self.tz = tz
        self._memo: dict[tuple[str, _Types], Any] = {}
        self._group_memo: dict[tuple[str, _Types], Any] = {}

    @classmethod
    def for_players(cls, session: Session, player_ids: Iterable[int], tz: ZoneInfo) -> list[Self]:
        """One history per player, all sharing one memo of group reads."""
        histories = [cls(session, player_id, tz) for player_id in player_ids]
        for history in histories[1:]:
            history._group_memo = histories[0]._group_memo
        return histories

    def _remember(self, kind: str, types: _Types, load: Any) -> Any:
        memo = self._group_memo if kind == "group" else self._memo
        key = (kind, types)
        if key not in memo:
            memo[key] = load()
        return memo[key]

    def _party(self, kind: str) -> ColumnElement[bool] | None:
        if kind == "mine":
            return EventLog.actor_id == self.player_id
        if kind == "about_me":
            return EventLog.subject_id == self.player_id
        return None

    def _events(self, kind: str, types: _Types) -> list[LoggedEvent]:
        stmt = select(*_COLUMNS).where(EventLog.event_type.in_(types))
        party = self._party(kind)
        if party is not None:
            stmt = stmt.where(party)
        rows = self._session.execute(stmt.order_by(EventLog.id))
        return [to_event(row) for row in rows]

    def mine(self, *types: EventType) -> list[LoggedEvent]:
        return self._remember("mine", types, lambda: self._events("mine", types))

    def about_me(self, *types: EventType) -> list[LoggedEvent]:
        return self._remember("about_me", types, lambda: self._events("about_me", types))

    def group(self, *types: EventType) -> list[LoggedEvent]:
        return self._remember("group", types, lambda: self._events("group", types))

    def _local(self, at: datetime) -> date:
        return as_utc(at).astimezone(self.tz).date()

    def _midnight(self, day: date) -> datetime:
        """The UTC instant a local day starts, naive like the column."""
        return datetime.combine(day, time(), tzinfo=self.tz).astimezone(UTC).replace(tzinfo=None)

    def _hosted_days(self, stmt: Select[tuple[datetime, Any]]) -> set[date]:
        """A bot-started (HARD MODE) hosting is no one's activity. This
        check stays in Python: JSON paths in SQL differ between SQLite and
        MariaDB, and hostings are a small share of the log."""
        return {
            self._local(at) for at, data in self._session.execute(stmt) if not data.get("hard_mode")
        }

    def _my_days(self, types: _Types) -> set[date]:
        mine = EventLog.actor_id == self.player_id
        plain = tuple(t for t in types if t is not EventType.GAME_ACTIVATED)
        stmt = select(EventLog.occurred_at).where(EventLog.event_type.in_(plain), mine)
        days = {self._local(at) for at in self._session.scalars(stmt)}
        if EventType.GAME_ACTIVATED in types:
            hosted = select(EventLog.occurred_at, EventLog.data)
            days |= self._hosted_days(hosted.where(_HOSTED, mine))
        return days

    def my_days(self, *types: EventType) -> set[date]:
        return self._remember("my_days", types, lambda: self._my_days(types))

    def group_active_between(self, types: _Types, first: date, last: date) -> bool:
        """Did anyone do any of `types` on a local day from `first` to
        `last`? An indexed range probe, so a streak never reads the whole
        group's history."""
        start, end = self._midnight(first), self._midnight(last + timedelta(days=1))
        within = and_(EventLog.occurred_at >= start, EventLog.occurred_at < end)
        plain = tuple(t for t in types if t is not EventType.GAME_ACTIVATED)
        probe = select(EventLog.id).where(EventLog.event_type.in_(plain), within).limit(1)
        if self._session.scalar(probe) is not None:
            return True
        if EventType.GAME_ACTIVATED not in types:
            return False
        hosted = select(EventLog.occurred_at, EventLog.data).where(_HOSTED, within)
        return bool(self._hosted_days(hosted))
