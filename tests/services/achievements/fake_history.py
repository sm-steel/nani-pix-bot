"""An in-memory History for testing progress functions without a DB."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any
from zoneinfo import ZoneInfo

from nani_pix_bot.models.enums import EventType
from nani_pix_bot.services.events import LoggedEvent

_ids = count(1)
_EPOCH = datetime(2026, 10, 5, 12, tzinfo=UTC)


def ev(
    event_type: EventType, actor: int | None = None, subject: int | None = None, **data: Any
) -> LoggedEvent:
    """`game=` and `at=` ride along in `data` and are popped off it."""
    event_id = next(_ids)
    game = data.pop("game", None)
    at = data.pop("at", None) or _EPOCH + timedelta(minutes=event_id)
    return LoggedEvent(event_id, event_type, actor, subject, game, data, at)


@dataclass
class FakeHistory:
    player_id: int
    events: list[LoggedEvent] = field(default_factory=list)
    tz: ZoneInfo = field(default_factory=lambda: ZoneInfo("UTC"))

    def _of(self, types: tuple[EventType, ...]) -> list[LoggedEvent]:
        return sorted((e for e in self.events if e.event_type in types), key=lambda e: e.id)

    def mine(self, *types: EventType) -> list[LoggedEvent]:
        return [e for e in self._of(types) if e.actor_id == self.player_id]

    def about_me(self, *types: EventType) -> list[LoggedEvent]:
        return [e for e in self._of(types) if e.subject_id == self.player_id]

    def group(self, *types: EventType) -> list[LoggedEvent]:
        return self._of(types)
