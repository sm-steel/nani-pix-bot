"""add reveal_video

Revision ID: c8e2a4f6b1d3
Revises: b7d9f1a3c5e8
Create Date: 2026-10-08 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8e2a4f6b1d3"
down_revision: str | Sequence[str] | None = "b7d9f1a3c5e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "reveal_video",
        sa.Column("slot", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column(
            "effect",
            sa.Enum("IRIS", "TILE_FLIP", "RIPPLE", "GLITCH", "SHATTER", name="revealeffect"),
            nullable=False,
        ),
        sa.Column("image_choice", sa.String(length=1), nullable=True),
        sa.Column(
            "status",
            sa.Enum("PENDING", "READY", "FAILED", name="revealstatus"),
            nullable=False,
        ),
        # LONGBLOB on MariaDB, like games.image_data.
        sa.Column("part1_ts", sa.LargeBinary(length=2**32 - 1), nullable=True),
        sa.Column("join_offset", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("ready_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("slot"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("reveal_video")
    sa.Enum(name="revealstatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="revealeffect").drop(op.get_bind(), checkfirst=True)
