from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nani_pix_bot.models.base import Base
from nani_pix_bot.models.enums import PeriodType

PERIOD_TYPE_LENGTH = 8
PERIOD_KEY_LENGTH = 16


class PeriodResult(Base):
    """One player's place on a finished period's podium, frozen when the
    period closed (services/achievements/periods.py's finalize). `rank` is
    the competition rank, so tied players share it (1, 1, 3)."""

    __tablename__ = "period_results"
    __table_args__ = (
        UniqueConstraint("period_type", "period_key", "player_id", name="uq_period_result_player"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    period_type: Mapped[PeriodType] = mapped_column(String(PERIOD_TYPE_LENGTH))
    period_key: Mapped[str] = mapped_column(String(PERIOD_KEY_LENGTH))
    rank: Mapped[int]
    player_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("players.telegram_user_id"))
    score: Mapped[int]
    wins: Mapped[int]


class PeriodState(Base):
    """Per period type: when the next period to close ends (UTC). Created at
    first start-up with the period then running, so no period before the
    deploy is ever scored (no backfill). A late start catches up from here."""

    __tablename__ = "period_state"

    period_type: Mapped[PeriodType] = mapped_column(String(PERIOD_TYPE_LENGTH), primary_key=True)
    next_end: Mapped[datetime]
