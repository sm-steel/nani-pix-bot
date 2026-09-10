"""add setup_step to games

Revision ID: e7c1f9a4b8d2
Revises: d3f4a1b2c5e7
Create Date: 2026-09-10 04:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7c1f9a4b8d2"
down_revision: str | Sequence[str] | None = "d3f4a1b2c5e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default is required: adding a NOT NULL column to a table
    # that already has rows fails on MariaDB otherwise. Existing rows
    # backfill to PICKING_METHOD — only meaningful for SETUP games, and
    # any live SETUP row predates the confirmation-screen flow anyway.
    op.add_column(
        "games",
        sa.Column(
            "setup_step",
            sa.Enum(
                "PICKING_METHOD",
                "AWAITING_PHOTO_CHANGE",
                "AWAITING_SYNONYM",
                "CONFIRMING",
                name="setupstep",
            ),
            nullable=False,
            server_default="PICKING_METHOD",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("games", "setup_step")
