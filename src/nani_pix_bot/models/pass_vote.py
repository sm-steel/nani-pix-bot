"""One player's vote to give up on a game's screenshot in one round (#428)."""

from datetime import UTC, datetime

from sqlalchemy import BigInteger, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nani_pix_bot.models.base import Base


class PassVote(Base):
    """A `/pass` vote. `stage` is the round it was cast in (the PixelStage number, or the
    hard-mode turn): only the current round's votes count, so a stage advance resets the
    tally without deleting anything. A /stop-deleted game drops its votes."""

    __tablename__ = "pass_votes"
    __table_args__ = (
        UniqueConstraint("game_id", "player_id", "stage", name="uq_pass_votes_game_player_stage"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    player_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("players.telegram_user_id"))
    stage: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
