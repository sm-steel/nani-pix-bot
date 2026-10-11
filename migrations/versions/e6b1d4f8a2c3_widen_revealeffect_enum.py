"""widen revealeffect enum with the Sakura Season transitions

Revision ID: e6b1d4f8a2c3
Revises: d5a9c3e7f1b2
Create Date: 2026-10-11 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e6b1d4f8a2c3"
down_revision: str | Sequence[str] | None = "d5a9c3e7f1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BASE = ("IRIS", "TILE_FLIP", "RIPPLE", "GLITCH", "SHATTER")
_SAKURA = ("SPRING_BREEZE",)


def upgrade() -> None:
    """Add the Sakura Season transitions to reveal_video.effect's native ENUM.
    MariaDB has no "add one literal" DDL for an existing ENUM column, so the
    whole allowed-value list is restated."""
    op.alter_column(
        "reveal_video",
        "effect",
        existing_type=sa.Enum(*_BASE, name="revealeffect"),
        type_=sa.Enum(*_BASE, *_SAKURA, name="revealeffect"),
        existing_nullable=False,
    )


def downgrade() -> None:
    """Back to the base effects. The single reveal slot is rewritten every game;
    one holding a Sakura transition is pointed at IRIS first so the narrower
    ENUM accepts it."""
    reveal_video = sa.table("reveal_video", sa.column("effect", sa.String()))
    op.execute(
        reveal_video.update().where(reveal_video.c.effect.in_(_SAKURA)).values(effect="IRIS")
    )
    op.alter_column(
        "reveal_video",
        "effect",
        existing_type=sa.Enum(*_BASE, *_SAKURA, name="revealeffect"),
        type_=sa.Enum(*_BASE, name="revealeffect"),
        existing_nullable=False,
    )
