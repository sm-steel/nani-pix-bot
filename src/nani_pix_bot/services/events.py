"""The event log's write side (issue: event log). `emit` appends one
`event_log` row in the caller's transaction and returns it as a
`LoggedEvent`. Telegram-free; every hook calls it from the one function
that already owns the state change (see the spec, §1)."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

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


def _utc(at: datetime) -> datetime:
    # DATETIME columns round-trip naive; they're UTC by convention.
    return at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)


def to_event(row: EventLog) -> LoggedEvent:
    return LoggedEvent(
        id=row.id,
        event_type=EventType(row.event_type),
        actor_id=row.actor_id,
        subject_id=row.subject_id,
        game_id=row.game_id,
        data=dict(row.data or {}),
        occurred_at=_utc(row.occurred_at),
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
