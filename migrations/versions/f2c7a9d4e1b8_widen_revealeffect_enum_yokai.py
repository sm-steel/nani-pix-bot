"""widen revealeffect enum with the Yōkai Night transitions

Revision ID: f2c7a9d4e1b8
Revises: b3e8f1a6c4d7
Create Date: 2026-10-27 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2c7a9d4e1b8"
down_revision: str | Sequence[str] | None = "b3e8f1a6c4d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BASE = ("IRIS", "TILE_FLIP", "RIPPLE", "GLITCH", "SHATTER")
_SAKURA = ("SPRING_BREEZE", "BLOSSOM_BRANCH")
_YOKAI = ("CANDLE_SNUFF", "NIGHT_PARADE")


def upgrade() -> None:
    """Add the Yōkai Night transitions to reveal_video.effect's native ENUM.
    MariaDB has no "add one literal" DDL for an existing ENUM column, so the
    whole allowed-value list is restated."""
    op.alter_column(
        "reveal_video",
        "effect",
        existing_type=sa.Enum(*_BASE, *_SAKURA, name="revealeffect"),
        type_=sa.Enum(*_BASE, *_SAKURA, *_YOKAI, name="revealeffect"),
        existing_nullable=False,
    )


def downgrade() -> None:
    """Back to the Sakura list. The single reveal slot is rewritten every game;
    one holding a Yōkai transition is pointed at IRIS first so the narrower
    ENUM accepts it."""
    reveal_video = sa.table("reveal_video", sa.column("effect", sa.String()))
    op.execute(reveal_video.update().where(reveal_video.c.effect.in_(_YOKAI)).values(effect="IRIS"))
    op.alter_column(
        "reveal_video",
        "effect",
        existing_type=sa.Enum(*_BASE, *_SAKURA, *_YOKAI, name="revealeffect"),
        type_=sa.Enum(*_BASE, *_SAKURA, name="revealeffect"),
        existing_nullable=False,
    )
