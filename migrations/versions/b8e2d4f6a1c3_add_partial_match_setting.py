"""add partial_match_min_letters to bot_settings

Revision ID: b8e2d4f6a1c3
Revises: a3f9c2d7e1b4
Create Date: 2026-10-06 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8e2d4f6a1c3"
down_revision: str | None = "a3f9c2d7e1b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bot_settings",
        sa.Column("partial_match_min_letters", sa.Integer(), nullable=False, server_default="4"),
    )


def downgrade() -> None:
    op.drop_column("bot_settings", "partial_match_min_letters")
