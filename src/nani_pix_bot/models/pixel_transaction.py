from datetime import UTC, datetime

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from nani_pix_bot.models.base import Base
from nani_pix_bot.models.enums import PixelReason

# Longest PixelReason value plus headroom for later phases' reasons.
REASON_LENGTH = 32
# Tile indexes / screenshot URLs (phase 2) — generous headroom.
DETAIL_LENGTH = 255


class PixelTransaction(Base):
    """One balance change — the append-only ledger behind `Player.pixels`.
    Every write goes through services/economy/wallet.py, which updates the
    balance and adds the row in the same transaction. The per-game wrong-guess
    cap is answered by summing these rows, so nothing about earnings is held in memory.

    `game_id` is SET NULL on delete: /stop and setup-abandon delete Game
    rows, and the ledger must outlive them."""

    __tablename__ = "pixel_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("players.telegram_user_id"), index=True
    )
    game_id: Mapped[int | None] = mapped_column(
        ForeignKey("games.id", ondelete="SET NULL"), default=None, index=True
    )
    amount: Mapped[int]
    reason: Mapped[PixelReason] = mapped_column(String(REASON_LENGTH))
    detail: Mapped[str | None] = mapped_column(String(DETAIL_LENGTH), default=None)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
