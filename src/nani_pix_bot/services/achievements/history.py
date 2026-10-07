"""History backed by event_log — what progress functions read in production."""

from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import EventType
from nani_pix_bot.models.event_log import EventLog
from nani_pix_bot.services.events import LoggedEvent, to_event


class DbHistory:
    def __init__(self, session: Session, player_id: int, tz: ZoneInfo) -> None:
        self._session = session
        self.player_id = player_id
        self.tz = tz

    def _query(
        self, types: tuple[EventType, ...], party: ColumnElement[bool] | None
    ) -> list[LoggedEvent]:
        stmt = select(EventLog).where(EventLog.event_type.in_(types))
        if party is not None:
            stmt = stmt.where(party)
        return [to_event(row) for row in self._session.scalars(stmt.order_by(EventLog.id))]

    def mine(self, *types: EventType) -> list[LoggedEvent]:
        return self._query(types, EventLog.actor_id == self.player_id)

    def about_me(self, *types: EventType) -> list[LoggedEvent]:
        return self._query(types, EventLog.subject_id == self.player_id)

    def group(self, *types: EventType) -> list[LoggedEvent]:
        return self._query(types, None)
