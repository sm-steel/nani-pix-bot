"""add clue_screenshot_handouts

Revision ID: 4d7b29a0997a
Revises: a5c7e9b1d3f6
Create Date: 2026-10-17 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4d7b29a0997a"
down_revision: str | Sequence[str] | None = "a5c7e9b1d3f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema, then seed one handout per screenshot already sold in a
    game (fingerprint unknown), so a round in progress keeps excluding them."""
    op.create_table(
        "clue_screenshot_handouts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("screenshot_url", sa.String(length=1024), nullable=False),
        sa.Column("fingerprint", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "game_id", "screenshot_url", name="uq_clue_screenshot_handouts_game_url"
        ),
    )
    op.create_index(
        op.f("ix_clue_screenshot_handouts_game_id"), "clue_screenshot_handouts", ["game_id"]
    )
    op.execute(
        "INSERT INTO clue_screenshot_handouts (game_id, screenshot_url, created_at)"
        " SELECT game_id, screenshot_url, MIN(created_at) FROM clue_purchases"
        " WHERE screenshot_url IS NOT NULL GROUP BY game_id, screenshot_url"
    )


def downgrade() -> None:
    """Downgrade schema. Just drop_table: dropping the game_id index first fails on MariaDB
    (error 1553, the foreign key still needs it; see c8e0a2b4d6f1)."""
    op.drop_table("clue_screenshot_handouts")
