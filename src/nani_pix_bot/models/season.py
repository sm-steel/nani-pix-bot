from datetime import UTC, datetime

from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from nani_pix_bot.models.base import Base
from nani_pix_bot.models.enums import SeasonStatus

RUN_ID_LENGTH = 48
SEASON_STATUS_LENGTH = 16


class SeasonSchedule(Base):
    """One scheduling of a season run (seasons spec §1). Every row is kept —
    cancelled and ended ones are the history. At most one row is open
    (scheduled/active/closing); services/seasons/schedule.py enforces it."""

    __tablename__ = "season_schedule"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(RUN_ID_LENGTH), index=True)
    start_at: Mapped[datetime]
    end_at: Mapped[datetime]
    status: Mapped[SeasonStatus] = mapped_column(String(SEASON_STATUS_LENGTH), index=True)
    created_by: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    ended_at: Mapped[datetime | None] = mapped_column(default=None)
