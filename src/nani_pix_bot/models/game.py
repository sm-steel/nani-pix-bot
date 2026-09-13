from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, ForeignKey, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nani_pix_bot.models.base import Base
from nani_pix_bot.models.enums import GameStatus, PixelStage, SetupStep
from nani_pix_bot.models.player import Player

# Generous headroom over any observed anime title length.
TITLE_LENGTH = 255
# "shikimori"/"jikan"/"tmdb" plus headroom — same length as `source` below.
SCREENSHOT_SOURCE_LENGTH = 16
# A length this large just tells MariaDB to pick LONGBLOB over
# BLOB/MEDIUMBLOB (see services/pixelate.py's docstring on why no image
# bytes are ever this large in practice, but LONGBLOB costs nothing
# extra to declare) — see ARCHITECTURE.md's schema table.
IMAGE_COLUMN_LENGTH = 2**32 - 1


class Game(Base):
    """One round of the guessing game — see MECHANICS.md and ARCHITECTURE.md."""

    __tablename__ = "games"

    id: Mapped[int] = mapped_column(primary_key=True)
    starter_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("players.telegram_user_id"))
    anilist_id: Mapped[int | None] = mapped_column(default=None)
    # One column per identification provider — reused for a screenshot
    # cross-search's resolved id too, even when that provider wasn't
    # the identification source (see screenshot_source below, and
    # MECHANICS.md's "Starting a game" section).
    shikimori_id: Mapped[int | None] = mapped_column(default=None)
    jikan_id: Mapped[int | None] = mapped_column(default=None)
    tmdb_id: Mapped[int | None] = mapped_column(default=None)
    title_romaji: Mapped[str | None] = mapped_column(String(TITLE_LENGTH), default=None)
    title_english: Mapped[str | None] = mapped_column(String(TITLE_LENGTH), default=None)
    title_native: Mapped[str | None] = mapped_column(String(TITLE_LENGTH), default=None)
    title_russian: Mapped[str | None] = mapped_column(String(TITLE_LENGTH), default=None)
    synonyms: Mapped[list[str] | None] = mapped_column(JSON, default=None)
    # Which identification method staged this game's title/synonyms —
    # "anilist" / "shikimori" / "jikan" / "tmdb" / "manual". See
    # services/game/state.py's stage_result().
    source: Mapped[str] = mapped_column(String(16), default="anilist")
    # Which provider's *_id column above is currently backing
    # original_image, if it's API-sourced at all ("shikimori"/"jikan"/
    # "tmdb", or None for a genuine upload). Also doubles as "which
    # provider the screenshot picker is currently resolving" *before*
    # original_image exists yet — set the moment a screenshot-source
    # button is tapped (commands/dm_start/screenshots.py), so a
    # cross-provider resolution's follow-up text message (the "Wrong
    # anime? Search again" correction) still knows which provider it's
    # searching without needing separate DB state for it (see issue
    # #11 — flow state has to be DB-derived, never only in-memory).
    # None means "no screenshot provider chosen yet" (genuine upload,
    # or still on the source-selection keyboard).
    screenshot_source: Mapped[str | None] = mapped_column(
        String(SCREENSHOT_SOURCE_LENGTH), default=None
    )
    # Only meaningful while status is SETUP — see SetupStep's docstring.
    setup_step: Mapped[SetupStep] = mapped_column(default=SetupStep.PICKING_METHOD)
    # The current game's original screenshot, stored directly rather
    # than as a Telegram file_id — removes any dependency on Telegram
    # continuing to serve a given file_id for the life of a game (a
    # deleted message, an expired id, etc.), and makes the traditional
    # upload flow and an API-picked screenshot fully symmetric: both
    # just need "obtain bytes once, store them." Deferred so routine
    # `games` queries (status checks, the /guess hot path) don't pull a
    # multi-hundred-KB blob every time. Cleared once the reveal message
    # is confirmed sent — see MECHANICS.md's "Cleanup" note under
    # Winning/Ending unsolved.
    original_image: Mapped[bytes | None] = mapped_column(
        LargeBinary(length=IMAGE_COLUMN_LENGTH), deferred=True, default=None
    )
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
    # Reset on every /guess (right or wrong) — see services/game/state.py's
    # INACTIVITY_NUDGE_DELAY/INACTIVITY_ADVANCE_DELAY. Only meaningful
    # while status is ACTIVE.
    inactivity_nudge_at: Mapped[datetime | None] = mapped_column(default=None)
    inactivity_advance_at: Mapped[datetime | None] = mapped_column(default=None)

    starter: Mapped[Player] = relationship(foreign_keys=[starter_id])
    winner: Mapped[Player | None] = relationship(foreign_keys=[winner_id])
