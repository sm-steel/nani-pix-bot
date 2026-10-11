"""add anime_tags to games

Revision ID: a2d6f8b4c1e9
Revises: f7c1d9e3a5b8
Create Date: 2026-10-10 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a2d6f8b4c1e9"
down_revision: str | Sequence[str] | None = "f7c1d9e3a5b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("games", sa.Column("anime_tags", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("games", "anime_tags")
