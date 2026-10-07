from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from nani_pix_bot.models.base import Base
from nani_pix_bot.models.enums import EventType

# Longest EventType value plus headroom.
EVENT_TYPE_LENGTH = 32


class EventLog(Base):
    """One domain event, appended in the same transaction as the state change
    it describes (services/events.py). Generic on purpose: achievements read it
    today, any later statistic can too. It started empty when it was deployed,
    which is what makes achievements ignore everything before that.

    Like `currency_transfers.game_id`, `game_id` is a plain integer rather
    than a foreign key, so rows outlive a /stop'ed (deleted) game. `actor_id`
    is who caused the event, `subject_id` the other person it is about (the
    host of a won game, a tip's recipient, a vote's candidate)."""

    __tablename__ = "event_log"
    __table_args__ = (
        Index("ix_event_log_type_actor", "event_type", "actor_id"),
        Index("ix_event_log_type_subject", "event_type", "subject_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC), index=True)
    event_type: Mapped[EventType] = mapped_column(String(EVENT_TYPE_LENGTH))
    actor_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    subject_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    game_id: Mapped[int | None] = mapped_column(default=None, index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
