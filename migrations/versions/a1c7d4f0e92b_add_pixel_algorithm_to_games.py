"""add pixel_algorithm to games

Revision ID: a1c7d4f0e92b
Revises: e5f1c8a2d4b7
Create Date: 2026-09-16 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1c7d4f0e92b"
down_revision: str | Sequence[str] | None = "e5f1c8a2d4b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default is required: adding a NOT NULL column to a table
    # that already has rows fails on MariaDB otherwise. Existing rows
    # backfill to MEDIAN, the same value a new game gets — so a round
    # already in flight at migration time renders its remaining stages
    # with the new default rather than the NEAREST its earlier stages
    # used. Deliberate: uniform is simpler to reason about, and a live
    # round straddling the migration is a narrow, one-off case.
    op.add_column(
        "games",
        sa.Column(
            "pixel_algorithm",
            sa.Enum("NEAREST", "BOX", "MEDIAN", "MODE", "LANCZOS", name="pixelalgorithm"),
            nullable=False,
            server_default="MEDIAN",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("games", "pixel_algorithm")
