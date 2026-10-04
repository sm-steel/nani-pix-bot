from datetime import UTC, datetime

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from nani_pix_bot.models.base import Base
from nani_pix_bot.models.enums import ClueKind

# Longest ClueKind value plus headroom.
KIND_LENGTH = 16


class CluePurchase(Base):
    """One clue a player bought in one game. Purchase state (which tile, which
    screenshot, the delivered image's file_id for re-sharing, whether it was
    shared) lives here, never in the currency ledger. `transfer_id` is the
    `currency_transfers` charge that paid for it; a refund names that charge via
    `reverses_id`."""

    __tablename__ = "clue_purchases"

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    player_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("players.telegram_user_id"), index=True
    )
    kind: Mapped[ClueKind] = mapped_column(String(KIND_LENGTH))
    tile_index: Mapped[int | None] = mapped_column(default=None)
    screenshot_url: Mapped[str | None] = mapped_column(String(1024), default=None)
    telegram_file_id: Mapped[str | None] = mapped_column(String(256), default=None)
    shared_at: Mapped[datetime | None] = mapped_column(default=None)
    transfer_id: Mapped[int] = mapped_column(ForeignKey("currency_transfers.id"), unique=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
