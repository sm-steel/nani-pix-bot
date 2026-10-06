"""add hard-mode clue discount

Revision ID: e6a2b8c4d9f1
Revises: d1c5e9a7b3f2
Create Date: 2026-10-06 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e6a2b8c4d9f1"
down_revision: str | Sequence[str] | None = "d1c5e9a7b3f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "games",
        sa.Column("hard_mode_clue_discount", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("games", "hard_mode_clue_discount")
