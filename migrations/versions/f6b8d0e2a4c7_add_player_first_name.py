"""add player first name

Revision ID: f6b8d0e2a4c7
Revises: e5a7c9d1f3b6
Create Date: 2026-10-07 17:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6b8d0e2a4c7"
down_revision: str | Sequence[str] | None = "e5a7c9d1f3b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("players", sa.Column("first_name", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("players", "first_name")
