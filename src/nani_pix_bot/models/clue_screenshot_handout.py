from datetime import UTC, datetime

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from nani_pix_bot.models.base import Base


class ClueScreenshotHandout(Base):
    """A screenshot the clue shop handed out in a game (issue #446): the
    record that keeps it from being sold again in that game, to anyone.
    Written with the CluePurchase but never deleted by a refund — the buyer
    has already seen the image. `fingerprint` is its perceptual hash
    (services/pixelate/fingerprint.py), so a copy behind another URL is
    caught too; NULL for rows backfilled from purchases made before it.
    (game_id, screenshot_url) is unique by the shop's own check before a
    charge, not by a DB constraint: a 1024-character utf8mb4 column is too
    long for a plain InnoDB unique key, and updates are handled one at a
    time, so two buys of one URL cannot overlap."""

    __tablename__ = "clue_screenshot_handouts"

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    screenshot_url: Mapped[str] = mapped_column(String(1024))
    fingerprint: Mapped[int | None] = mapped_column(BigInteger, default=None)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
