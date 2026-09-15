from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import JSON, BigInteger, ForeignKey, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nani_pix_bot.models.base import Base
from nani_pix_bot.models.enums import GameStatus, PixelStage, Provider, SetupStep
from nani_pix_bot.models.player import Player

# Generous headroom over any observed anime title length.
TITLE_LENGTH = 255
# A Provider value plus headroom — same length as `source` below.
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
    # Which identification method staged this game's title/synonyms — a
    # Provider, or "manual", which is deliberately outside that enum
    # because it names the *absence* of an automatic provider rather than
    # one of them (see Provider's docstring). See
    # services/game/state.py's stage_result()/stage_manual_entry().
    #
    # The explicit String(16) on this column, and String(...) on the two
    # screenshot columns below, are load-bearing and must stay: drop one
    # and SQLAlchemy auto-infers a native sa.Enum(Provider) column from
    # the annotation instead, which persists member *names*
    # ("SHIKIMORI") rather than the values every existing row already
    # holds — with no migration behind it and no error until something
    # reads the column back. GameStatus/PixelStage/SetupStep genuinely
    # do get native sa.Enum columns, created by their own migrations;
    # these three deliberately must not. Guarded by
    # tests/models/test_game.py's
    # test_provider_columns_store_the_value_not_the_member_name.
    source: Mapped[Provider | Literal["manual"]] = mapped_column(String(16), default="anilist")
    # Image provenance, and nothing else: which provider's *_id column
    # above is currently backing original_image, or None when there is
    # no API-sourced image — a genuine
    # upload, or no image staged yet. Written only where original_image
    # itself is written from a gallery pick
    # (commands/dm_start/screenshot_gallery.py).
    screenshot_source: Mapped[Provider | None] = mapped_column(
        String(SCREENSHOT_SOURCE_LENGTH), default=None
    )
    # Picker state, and nothing else: which provider the screenshot
    # picker is currently *resolving* an anime for, i.e. which provider
    # a typed DM message would be searched against. Set when a
    # screenshot-source button is tapped (commands/dm_start/
    # screenshots.py) and kept for as long as a typed query is still
    # meaningful — a cross-provider gallery's "Wrong anime? Search
    # again" correction, or any failure screen the starter was dropped
    # back onto — so that flow state stays DB-derived rather than
    # in-memory (see issue #11). Cleared once there is nothing left to
    # resolve: a same-provider gallery (the id came from identification,
    # and no correction is offered) or a finished pick. Deliberately
    # *not* screenshot_source: the picker being on a provider says
    # nothing about where the stored image came from, and conflating
    # the two let a genuine upload delete an identification provider id
    # (see MECHANICS.md's "Starting a game").
    screenshot_picker_provider: Mapped[Provider | None] = mapped_column(
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
