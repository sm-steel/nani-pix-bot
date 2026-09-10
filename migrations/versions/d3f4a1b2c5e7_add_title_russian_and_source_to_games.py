"""add title_russian and source to games

Revision ID: d3f4a1b2c5e7
Revises: a5ef10f74e63
Create Date: 2026-09-10 03:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3f4a1b2c5e7"
down_revision: str | Sequence[str] | None = "a5ef10f74e63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("games", sa.Column("title_russian", sa.String(length=255), nullable=True))
    # server_default is required: adding a NOT NULL column to a table that
    # already has rows (the live games table does) fails on MariaDB
    # otherwise. Existing rows backfill to "anilist" — every pre-#17 game
    # was, in fact, sourced from AniList.
    op.add_column(
        "games",
        sa.Column("source", sa.String(length=16), nullable=False, server_default="anilist"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("games", "source")
    op.drop_column("games", "title_russian")
