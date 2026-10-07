from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from nani_pix_bot.models.base import Base
from nani_pix_bot.models.enums import OutboxKind

KIND_LENGTH = 16


class AnnouncementOutbox(Base):
    """A group post owed but not yet sent (jobs/announcements.py). Written in
    the same transaction as what it announces, so a crash between the two
    can neither lose the post nor send it for something that rolled back.
    Posted in id order; `batch_id` (the event that caused an unlock) groups
    several unlocks from one moment into one album."""

    __tablename__ = "announcement_outbox"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[OutboxKind] = mapped_column(String(KIND_LENGTH))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    batch_id: Mapped[int | None] = mapped_column(default=None)
    attempts: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    posted_at: Mapped[datetime | None] = mapped_column(default=None, index=True)
