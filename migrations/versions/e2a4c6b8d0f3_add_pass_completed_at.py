"""add season_pass.free_completed_at

Revision ID: e2a4c6b8d0f3
Revises: d9f1b3c5e7a2
Create Date: 2026-10-14 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2a4c6b8d0f3"
down_revision: str | Sequence[str] | None = "d9f1b3c5e7a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("season_pass", sa.Column("free_completed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("season_pass", "free_completed_at")
