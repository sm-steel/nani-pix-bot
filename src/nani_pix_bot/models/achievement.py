from datetime import UTC, datetime

from sqlalchemy import BigInteger, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nani_pix_bot.models.base import Base
from nani_pix_bot.models.enums import Rarity

# Longest catalogue key plus headroom.
KEY_LENGTH = 48
# "2026-W41" and the like; '' for a non-period grant.
PERIOD_KEY_LENGTH = 16
RARITY_LENGTH = 16


class AchievementGrant(Base):
    """One achievement tier a player unlocked (services/achievements/engine.py).
    `period_key` is '' unless this is a champion grant ("2026-10"), never NULL:
    MariaDB lets NULLs repeat in a unique index, which would let a tier be
    granted twice. `reward` is what was paid; `transfer_id` its ledger row
    (NULL when the reward was 0)."""

    __tablename__ = "achievement_grants"
    __table_args__ = (
        UniqueConstraint("player_id", "key", "tier", "period_key", name="uq_achievement_grant"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("players.telegram_user_id"), index=True
    )
    key: Mapped[str] = mapped_column(String(KEY_LENGTH))
    tier: Mapped[int]
    period_key: Mapped[str] = mapped_column(String(PERIOD_KEY_LENGTH), default="")
    rarity: Mapped[Rarity] = mapped_column(String(RARITY_LENGTH))
    reward: Mapped[int]
    points: Mapped[int]
    transfer_id: Mapped[int | None] = mapped_column(
        ForeignKey("currency_transfers.id"), default=None
    )
    granted_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))


class AchievementClaim(Base):
    """A group-unique tier's single holder (Pioneer, Milestone Keeper #N) —
    the primary key is what makes a second holder impossible."""

    __tablename__ = "achievement_claims"

    key: Mapped[str] = mapped_column(String(KEY_LENGTH), primary_key=True)
    tier: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("players.telegram_user_id"))
