from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nani_pix_bot.models.base import Base
from nani_pix_bot.models.enums import GameStatus, PixelStage, SetupStep
from nani_pix_bot.models.player import Player

# Generous headroom over any observed anime title / Telegram file_id length.
TITLE_LENGTH = 255
FILE_ID_LENGTH = 512


class Game(Base):
    """One round of the guessing game — see MECHANICS.md and ARCHITECTURE.md."""

    __tablename__ = "games"

    id: Mapped[int] = mapped_column(primary_key=True)
    starter_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("players.telegram_user_id"))
    anilist_id: Mapped[int | None] = mapped_column(default=None)
    title_romaji: Mapped[str | None] = mapped_column(String(TITLE_LENGTH), default=None)
    title_english: Mapped[str | None] = mapped_column(String(TITLE_LENGTH), default=None)
    title_native: Mapped[str | None] = mapped_column(String(TITLE_LENGTH), default=None)
    title_russian: Mapped[str | None] = mapped_column(String(TITLE_LENGTH), default=None)
    synonyms: Mapped[list[str] | None] = mapped_column(JSON, default=None)
    # Which identification method staged this game's title/synonyms —
    # "anilist" / "shikimori" / "manual". See services/game/state.py's
    # stage_result().
    source: Mapped[str] = mapped_column(String(16), default="anilist")
    # Only meaningful while status is SETUP — see SetupStep's docstring.
    setup_step: Mapped[SetupStep] = mapped_column(default=SetupStep.PICKING_METHOD)
    # Cleared once the reveal message is confirmed sent — see MECHANICS.md's
    # "Cleanup" note under Winning/Ending unsolved.
    original_file_id: Mapped[str | None] = mapped_column(String(FILE_ID_LENGTH), default=None)
    status: Mapped[GameStatus] = mapped_column(default=GameStatus.SETUP)
    current_stage: Mapped[PixelStage | None] = mapped_column(default=None)
    wrong_guess_count: Mapped[int] = mapped_column(default=0)
    # Never resets (unlike wrong_guess_count, which resets on stage
    # advance) — used to gate /correct on at least one real attempt.
    total_guess_count: Mapped[int] = mapped_column(default=0)
    winner_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("players.telegram_user_id"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    scheduled_end_at: Mapped[datetime | None] = mapped_column(default=None)
    ended_at: Mapped[datetime | None] = mapped_column(default=None)
    # created_at + 1h — see services/game/state.py's SETUP_ABANDON_DELAY. Only
    # meaningful while status is SETUP.
    setup_deadline: Mapped[datetime | None] = mapped_column(default=None)

    starter: Mapped[Player] = relationship(foreign_keys=[starter_id])
    winner: Mapped[Player | None] = relationship(foreign_keys=[winner_id])
