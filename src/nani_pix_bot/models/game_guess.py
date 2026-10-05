from datetime import UTC, datetime

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from nani_pix_bot.models.base import Base

# Telegram messages can be 4096 chars; a guess longer than this is noise —
# services/game/guesses.py truncates rather than rejecting it.
GUESS_TEXT_LENGTH = 255
# A rendered masked title (services/clues/text.py's words_shape) roughly
# doubles a title's length (spaced mask characters); TITLE_LENGTH is 255.
PARTIAL_REVEAL_LENGTH = 512


class GameGuess(Base):
    """One /guess on one game (issue #251): who guessed what, at which stage
    (or hard-mode turn), whether it was right, and the partial-match reveal
    it earned, if any. The hard-mode vote's ballot is built from these rows,
    and a non-NULL `partial_reveal` locks the title-shape clue for the game.
    Counts still come from `Game.total_guess_count`."""

    __tablename__ = "game_guesses"

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    player_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("players.telegram_user_id"), index=True
    )
    text: Mapped[str] = mapped_column(String(GUESS_TEXT_LENGTH))
    # PixelStage number (1-5), or the hard-mode turn (1-2).
    stage: Mapped[int]
    correct: Mapped[bool] = mapped_column(default=False)
    partial_reveal: Mapped[str | None] = mapped_column(String(PARTIAL_REVEAL_LENGTH), default=None)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
