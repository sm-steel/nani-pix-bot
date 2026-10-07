"""The event log's write side (issue: event log). `emit` appends one
`event_log` row in the caller's transaction and returns it as a
`LoggedEvent`. Telegram-free; every hook calls it from the one function
that already owns the state change (see the spec, §1)."""

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from loguru import logger
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import EventType
from nani_pix_bot.models.event_log import EventLog


@dataclass(frozen=True)
class Involved:
    """Who and what an event is about. All optional: an overthrow has only an
    actor, a pot payout has a game but a house-side payer."""

    actor_id: int | None = None
    subject_id: int | None = None
    game_id: int | None = None


@dataclass(frozen=True)
class LoggedEvent:
    id: int
    event_type: EventType
    actor_id: int | None
    subject_id: int | None
    game_id: int | None
    data: Mapping[str, Any]
    occurred_at: datetime


class EventRow(Protocol):
    """An event_log row, read-only: the ORM entity, or a plain column row
    with the same names (what a bulk history read selects)."""

    @property
    def id(self) -> int: ...
    @property
    def event_type(self) -> EventType | str: ...
    @property
    def actor_id(self) -> int | None: ...
    @property
    def subject_id(self) -> int | None: ...
    @property
    def game_id(self) -> int | None: ...
    @property
    def data(self) -> Mapping[str, Any] | str | None: ...
    @property
    def occurred_at(self) -> datetime: ...


class _JsonData(Mapping[str, Any]):
    """An event's data still as JSON text (a bulk history read selects it
    raw), decoded on first use: most of a year of group events are only
    counted or ordered, never looked into."""

    __slots__ = ("_raw", "_value")

    def __init__(self, raw: str) -> None:
        self._raw = raw
        self._value: dict[str, Any] | None = None

    def _decoded(self) -> dict[str, Any]:
        if self._value is None:
            self._value = json.loads(self._raw) or {}
        return self._value

    def __getitem__(self, key: str) -> Any:
        return self._decoded()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._decoded())

    def __len__(self) -> int:
        return len(self._decoded())

    def __repr__(self) -> str:
        return repr(self._decoded())


def _data(raw: Mapping[str, Any] | str | None) -> Mapping[str, Any]:
    return _JsonData(raw) if isinstance(raw, str) else dict(raw or {})


def as_utc(at: datetime) -> datetime:
    # DATETIME columns round-trip naive; they're UTC by convention.
    return at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)


def to_event(row: EventRow) -> LoggedEvent:
    return LoggedEvent(
        id=row.id,
        event_type=EventType(row.event_type),
        actor_id=row.actor_id,
        subject_id=row.subject_id,
        game_id=row.game_id,
        data=_data(row.data),
        occurred_at=as_utc(row.occurred_at),
    )


def _dispatch(session: Session, event: LoggedEvent) -> None:
    """Hands the event to its one consumer, in a savepoint: a broken
    achievement must never roll back the player's own action, so a failure
    discards only the achievement work (nested savepoints cover a reward
    cascade, where this re-enters via wallet.credit). A function-local
    import: the achievements package imports this module (and, via
    wallet, emit())."""
    from nani_pix_bot.services import achievements

    try:
        with session.begin_nested():
            achievements.on_event(session, event)
    except Exception:
        logger.opt(exception=True).error(
            "achievements failed on event {event_type} (row {event_id}); action kept",
            event_type=event.event_type.value,
            event_id=event.id,
        )


def emit(session: Session, event_type: EventType, involved: Involved, **data: Any) -> LoggedEvent:
    row = EventLog(
        event_type=event_type,
        actor_id=involved.actor_id,
        subject_id=involved.subject_id,
        game_id=involved.game_id,
        data=data,
    )
    session.add(row)
    session.flush()
    # DEBUG: the state change itself is already logged at INFO by its owner.
    logger.debug(
        "event {event_type} logged (row {row_id})", event_type=event_type.value, row_id=row.id
    )
    event = to_event(row)
    _dispatch(session, event)
    return event
