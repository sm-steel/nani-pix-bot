from datetime import UTC, datetime

from sqlalchemy import BigInteger, ForeignKey, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from nani_pix_bot.models.base import Base
from nani_pix_bot.models.enums import Provider
from nani_pix_bot.models.game import IMAGE_COLUMN_LENGTH, SCREENSHOT_SOURCE_LENGTH


class ClueAuthorScreenshot(Base):
    """One screenshot of the author's clue list (issue #450): picked by a
    game's starter on the setup preview's clue panel, from a Shikimori/TMDB
    gallery (`source` and `url` set) or uploaded (both NULL). The shop sells
    these by the game's `clue_screenshot_mode` (services/clues/author_list.py).
    At most 10 per game, with no two the same frame (`fingerprint`, see
    services/pixelate/fingerprint.py) — both checked by author_list.add, not
    by the DB.

    `image` is the full-quality original, the source of the clue's
    pixelation and of its handout row's copy for the end-of-game album;
    cleared (set to NULL) after the reveal. The rows go with the game: a
    /stop or an abandoned setup deletes it, and the cascade takes them."""

    __tablename__ = "clue_author_screenshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    # A Provider value in a plain String, like Game.screenshot_source.
    source: Mapped[Provider | None] = mapped_column(String(SCREENSHOT_SOURCE_LENGTH), default=None)
    url: Mapped[str | None] = mapped_column(String(1024), default=None)
    image: Mapped[bytes | None] = mapped_column(
        LargeBinary(length=IMAGE_COLUMN_LENGTH), deferred=True, default=None
    )
    fingerprint: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
