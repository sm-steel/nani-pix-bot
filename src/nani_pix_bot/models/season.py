from datetime import UTC, datetime

from sqlalchemy import BigInteger, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nani_pix_bot.models.base import Base
from nani_pix_bot.models.enums import SeasonStatus, XpSource

RUN_ID_LENGTH = 48
SEASON_STATUS_LENGTH = 16
XP_SOURCE_LENGTH = 16


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


class SeasonXp(Base):
    """One Season XP award (seasons spec §2), append-only. Standings are
    SUM(amount) per player within a season."""

    __tablename__ = "season_xp"

    id: Mapped[int] = mapped_column(primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("season_schedule.id"), index=True)
    player_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("players.telegram_user_id"), index=True
    )
    amount: Mapped[int]
    source: Mapped[XpSource] = mapped_column(String(XP_SOURCE_LENGTH))
    game_id: Mapped[int | None] = mapped_column(default=None, index=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))


class SeasonResult(Base):
    """A finished season's frozen standings (seasons spec §2), one row per
    player with XP, written when the season finalizes."""

    __tablename__ = "season_results"
    __table_args__ = (UniqueConstraint("season_id", "player_id", name="uq_season_result_player"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("season_schedule.id"), index=True)
    player_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("players.telegram_user_id"))
    rank: Mapped[int]
    xp: Mapped[int]
