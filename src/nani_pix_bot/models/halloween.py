"""Tables for the season mechanics (hexes, ofuda, omikuji). Boss state, candle
progress and bones are derived from `games`, so they need no tables."""

from datetime import date, datetime

from sqlalchemy import BigInteger, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nani_pix_bot.models.base import Base

# The longest HexKind / HexOutcome / OmikujiKind value plus headroom.
KIND_LENGTH = 16


class Hex(Base):
    """One hex a player cast on another during a season. `expires_at` is set
    for pranks only; `game_id` is the game it triggered on (kappa/kitsune)."""

    __tablename__ = "hexes"
    __table_args__ = (
        Index("ix_hexes_target_id_resolved_at", "target_id", "resolved_at"),
        Index("ix_hexes_caster_id_kind_cast_at", "caster_id", "kind", "cast_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("season_schedule.id"))
    kind: Mapped[str] = mapped_column(String(KIND_LENGTH))
    caster_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("players.telegram_user_id"))
    target_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("players.telegram_user_id"))
    cast_at: Mapped[datetime]
    expires_at: Mapped[datetime | None] = mapped_column(default=None)
    resolved_at: Mapped[datetime | None] = mapped_column(default=None)
    outcome: Mapped[str | None] = mapped_column(String(KIND_LENGTH), default=None)
    game_id: Mapped[int | None] = mapped_column(
        ForeignKey("games.id", ondelete="SET NULL"), default=None
    )


class ArmedOfuda(Base):
    """A player's ofuda armed for one game: at most one per player per game."""

    __tablename__ = "armed_ofuda"

    player_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("players.telegram_user_id"), primary_key=True
    )
    game_id: Mapped[int] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), primary_key=True
    )
    armed_at: Mapped[datetime]


class OmikujiDraw(Base):
    """A player's omikuji draw; one per player per season per local day."""

    __tablename__ = "omikuji_draws"
    __table_args__ = (
        UniqueConstraint("season_id", "player_id", "local_date", name="uq_omikuji_draws_day"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("season_schedule.id"))
    player_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("players.telegram_user_id"))
    local_date: Mapped[date]
    outcome: Mapped[str] = mapped_column(String(KIND_LENGTH))
    drawn_at: Mapped[datetime]
