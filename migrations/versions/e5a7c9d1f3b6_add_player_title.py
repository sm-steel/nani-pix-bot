"""add player title

Revision ID: e5a7c9d1f3b6
Revises: d4f6b8c0e2a5
Create Date: 2026-10-07 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5a7c9d1f3b6"
down_revision: str | Sequence[str] | None = "d4f6b8c0e2a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("players", sa.Column("title_key", sa.String(96), nullable=True))


def downgrade() -> None:
    op.drop_column("players", "title_key")
